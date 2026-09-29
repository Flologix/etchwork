"""read_datasheet: a datasheet's pages as images, and its text layer, for the
agent's model to read (datasheet-parts design §5.1).

The PDFs are made here rather than committed: vendors' datasheets are their
copyright (design §8), and a two-line writer keeps every page's content known.
"""
import os

import pytest
from PIL import Image

from circuit_mcp import datasheet, events, paths, service

A4 = (595, 842)     # points


def make_pdf(pages, size=A4) -> bytes:
    """A minimal valid PDF: one page per entry, the entry's text in Helvetica,
    or (None) a page holding only a drawn rectangle -- a drawing, no text."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        if text is None:
            stream = "0 0 0 RG 2 w 100 100 300 300 re S"
        else:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = f"BT /F1 18 Tf 72 {size[1] - 100} Td ({safe}) Tj ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} {size[1]}] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


@pytest.fixture
def part_pdf(workspace):
    (workspace / "datasheets").mkdir()
    pdf = workspace / "datasheets" / "part.pdf"
    pdf.write_bytes(make_pdf(["ACME X1 Humidity Sensor", "Pin 1 VDD  Pin 2 GND", None]))
    return pdf


def test_every_page_is_rendered_and_the_text_kept(workspace, part_pdf):
    out = datasheet.read_datasheet("datasheets/part.pdf")

    assert out.startswith("READ datasheets/part.pdf: 3 pages"), out
    folder = workspace / "datasheets" / "part"
    assert sorted(p.name for p in folder.glob("page-*.png")) == [
        "page-1.png", "page-2.png", "page-3.png"]
    text = (folder / "text.txt").read_text()
    assert "ACME X1 Humidity Sensor" in text and "Pin 1 VDD" in text
    assert "--- page 3 ---" in text


def test_the_index_names_each_page_and_flags_a_page_with_no_text(workspace, part_pdf):
    out = datasheet.read_datasheet("datasheets/part.pdf")
    assert "p1: ACME X1 Humidity Sensor" in out, out
    assert "p2: Pin 1 VDD" in out, out
    assert "p3: (no text: a drawing or a scan)" in out, out


def test_pages_are_rendered_at_a_resolution_dimensions_can_be_read_from(workspace, part_pdf):
    datasheet.read_datasheet("datasheets/part.pdf")
    with Image.open(workspace / "datasheets" / "part" / "page-1.png") as page:
        assert page.size == (round(A4[0] * datasheet.DPI / 72), round(A4[1] * datasheet.DPI / 72))
    assert datasheet.DPI >= 200


def test_only_the_pages_asked_for_are_rendered_but_all_are_indexed(workspace, part_pdf):
    out = datasheet.read_datasheet("datasheets/part.pdf", pages="2-3")
    folder = workspace / "datasheets" / "part"
    assert sorted(p.name for p in folder.glob("page-*.png")) == ["page-2.png", "page-3.png"]
    assert "p1: ACME X1" in out


@pytest.mark.parametrize("pages", ["0", "4", "2-1", "x", "1,,2"])
def test_a_bad_page_selection_is_refused(workspace, part_pdf, pages):
    out = datasheet.read_datasheet("datasheets/part.pdf", pages=pages)
    assert out.startswith("pages must be"), out
    assert not (workspace / "datasheets" / "part").exists()


def test_a_long_datasheet_is_rendered_up_to_the_cap_and_says_how_to_get_the_rest(
        workspace, part_pdf, monkeypatch):
    monkeypatch.setattr(datasheet, "MAX_PAGES", 2)
    out = datasheet.read_datasheet("datasheets/part.pdf")
    assert sorted(p.name for p in (workspace / "datasheets" / "part").glob("page-*.png")) == [
        "page-1.png", "page-2.png"]
    assert "pages=" in out and "p3:" in out


def test_a_rerun_leaves_no_page_from_the_previous_datasheet(workspace, part_pdf):
    datasheet.read_datasheet("datasheets/part.pdf")
    part_pdf.write_bytes(make_pdf(["Revision B"]))
    out = datasheet.read_datasheet("datasheets/part.pdf")
    assert out.startswith("READ datasheets/part.pdf: 1 page ->"), out
    folder = workspace / "datasheets" / "part"
    assert [p.name for p in folder.glob("page-*.png")] == ["page-1.png"]
    assert "ACME" not in (folder / "text.txt").read_text()


def test_a_file_that_is_not_a_pdf_is_refused(workspace):
    (workspace / "notes.pdf").write_text("just some notes")
    out = datasheet.read_datasheet("notes.pdf")
    assert "is not a PDF" in out, out


def test_a_damaged_pdf_is_a_failure_not_a_crash(workspace):
    (workspace / "broken.pdf").write_bytes(b"%PDF-1.4\nthis is not really a pdf\n")
    out = datasheet.read_datasheet("broken.pdf")
    assert out.startswith("the datasheet could not be read"), out


def test_a_missing_or_outside_file_is_refused(workspace):
    assert datasheet.read_datasheet("nope.pdf").startswith("no such file in the workspace")
    assert "outside the project folder" in datasheet.read_datasheet("../x.pdf")


def test_an_output_folder_pointing_outside_the_project_is_refused(workspace, part_pdf,
                                                                    tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (workspace / "datasheets" / "part").symlink_to(outside, target_is_directory=True)
    out = datasheet.read_datasheet("datasheets/part.pdf")
    assert "outside the project folder" in out, out
    assert list(outside.iterdir()) == []


def test_a_datasheet_over_the_size_limit_is_refused(workspace, part_pdf, monkeypatch):
    monkeypatch.setattr(datasheet, "MAX_BYTES", 100)
    out = datasheet.read_datasheet("datasheets/part.pdf")
    assert "is larger than" in out, out


def test_a_render_that_times_out_is_a_short_failure(workspace, part_pdf, monkeypatch):
    monkeypatch.setattr(service, "TIMEOUT", 0.001)
    out = datasheet.read_datasheet("datasheets/part.pdf")
    assert out.startswith("the datasheet could not be read") and "timed out" in out, out
    assert "import" not in out


@pytest.mark.parametrize("answer, outcome", [
    ("READ datasheets/part.pdf: 3 pages -> datasheets/part/", "ok"),
    ("notes.pdf is not a PDF (it does not start with %PDF)", "refused"),
    ("datasheets/part.pdf is larger than 50 MB", "refused"),
    ("pages must be page numbers or ranges from 1 to 3, like 1-3,7", "refused"),
    ("the datasheet could not be read: Failed to load document", "failed"),
])
def test_read_datasheet_answers_are_logged_with_the_right_outcome(answer, outcome):
    assert events.outcome(answer) == outcome
