"""read_datasheet: a datasheet's pages as images, and its text layer.

The datasheet-parts design (§5.1). A part KiCad lacks is made from its
datasheet, and reading one is the agent's model's work: the pin table, the
land pattern, which view a drawing is in. What the engine does is make that
reading possible and repeatable -- every page rendered at a resolution
dimensions can be read from, the text layer extracted, and an index of what
is on which page -- so the model opens the pages it needs by name instead of
guessing at a 60-page PDF.

**Parsed in a subprocess.** PDFium is a C library, and the PDF is whatever
the user dropped in the project. A crash or a hang ends one call, with the
same timeout the pcbnew steps have, not the server.
"""
import json
import re
import subprocess

from circuit_mcp import paths, service

OUT = "datasheets"
# 200 dpi: an A4 page is 1654 x 2339 px, enough to read a 0.5 mm dimension
# off a drawing, and a page stays a few hundred KB.
DPI = 200
# Pages rendered when none are asked for. Text and the index cover every page.
MAX_PAGES = 40
MAX_BYTES = 50 * 1024 * 1024
# The index lists at most this many pages; the rest are in text.txt.
MAX_INDEX = 80

_STEM = re.compile(r"[^A-Za-z0-9._-]")

_READ = r"""
import json, pathlib, sys
import pypdfium2 as pdfium

source, folder, dpi, wanted = sys.argv[1], pathlib.Path(sys.argv[2]), int(sys.argv[3]), json.loads(sys.argv[4])
try:
    pdf = pdfium.PdfDocument(source)
except pdfium.PdfiumError as error:
    print(json.dumps({"error": str(error)}))
    sys.exit(0)
index, texts = [], []
for number in range(1, len(pdf) + 1):
    page = pdf[number - 1]
    text = page.get_textpage().get_text_bounded().replace("\r\n", "\n").replace("\r", "\n")
    texts.append(f"--- page {number} ---\n{text.strip()}\n")
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    index.append({"page": number, "chars": len(text.strip()), "first": first[:90]})
    if wanted is None or number in wanted:
        page.render(scale=dpi / 72).to_pil().save(folder / f"page-{number}.png")
(folder / "text.txt").write_text("\n".join(texts))
print(json.dumps({"pages": len(pdf), "index": index}))
"""


def _pages(selection: str, count: int) -> list[int] | None:
    """`"1-3,7"` as page numbers, or None when it is not a valid selection."""
    wanted = []
    for part in selection.split(","):
        found = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
        if not found:
            return None
        first, last = int(found.group(1)), int(found.group(2) or found.group(1))
        if not 1 <= first <= last <= count:
            return None
        wanted.extend(range(first, last + 1))
    return sorted(set(wanted))


def _count(source) -> int | str:
    """The page count, or why the file could not be opened."""
    code, out = service.python(
        "import sys, pypdfium2 as p\n"
        "try:\n    print(len(p.PdfDocument(sys.argv[1])))\n"
        "except p.PdfiumError as e:\n    print('error: ' + str(e))\n", str(source))
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0 or not last.isdigit():
        return last.removeprefix("error: ") or f"exit {code}"
    return int(last)


def read_datasheet(pdf: str, pages: str | None = None) -> str:
    try:
        source = paths.inside(pdf)
    except ValueError as error:
        return f"{pdf}: {error}"
    if not source.is_file():
        return f"no such file in the workspace: {pdf}"
    if source.stat().st_size > MAX_BYTES:
        return f"{pdf} is larger than {MAX_BYTES // (1024 * 1024)} MB"
    with open(source, "rb") as f:
        if f.read(5) != b"%PDF-":
            return f"{pdf} is not a PDF (it does not start with %PDF)"

    try:
        count = _count(source)
    except RuntimeError as error:
        return f"the datasheet could not be read: {error}"
    if isinstance(count, str):
        return f"the datasheet could not be read: {count}"

    if pages is not None:
        wanted = _pages(pages, count)
        if wanted is None:
            return (f"pages must be page numbers or ranges from 1 to {count}, "
                    f"like 1-3,7 (got {pages!r})")
    else:
        wanted = list(range(1, min(count, MAX_PAGES) + 1))

    stem = _STEM.sub("_", source.stem)
    try:
        folder = paths.inside(f"{OUT}/{stem}")
    except ValueError as error:
        return f"{OUT}/{stem}: {error}"
    if folder.exists() and not folder.is_dir():
        return f"{paths.rel(folder)} is a file, not a folder; move it, then read the datasheet again"
    folder.mkdir(parents=True, exist_ok=True)
    # A previous datasheet's pages must never pass for this one's.
    for old in [*folder.glob("page-*.png"), folder / "text.txt"]:
        if old.is_symlink() or old.is_file():
            old.unlink()

    try:
        code, out = service.python(_READ, str(source), str(folder), str(DPI), json.dumps(wanted))
    except RuntimeError as error:
        return f"the datasheet could not be read: {error}"
    answer = next((line for line in reversed(out.splitlines()) if line.startswith("{")), None)
    result = json.loads(answer) if answer else {"error": out[-300:] or f"exit {code}"}
    if "error" in result:
        return f"the datasheet could not be read: {result['error']}"

    where = paths.rel(folder)
    rendered = ", ".join(f"page-{n}.png" for n in wanted) if len(wanted) <= 6 else \
        f"page-{wanted[0]}.png … page-{wanted[-1]}.png ({len(wanted)} pages)"
    lines = [f"READ {pdf}: {result['pages']} page{'' if result['pages'] == 1 else 's'} -> {where}/",
             f"  rendered at {DPI} dpi: {rendered}",
             f"  text layer: {where}/text.txt"]
    if pages is None and count > MAX_PAGES:
        lines.append(f"  pages after {MAX_PAGES} were not rendered: call again with "
                     f"pages=\"{MAX_PAGES + 1}-{count}\" (or the ones you need)")
    lines.append("Pages:")
    for entry in result["index"][:MAX_INDEX]:
        what = entry["first"] if entry["chars"] else "(no text: a drawing or a scan)"
        lines.append(f"  p{entry['page']}: {what}")
    if count > MAX_INDEX:
        lines.append(f"  … {count - MAX_INDEX} more pages; their text is in text.txt")
    lines.append("Open the pages you need as images: read drawings and tables from the "
                 "picture, and cite the page for every value you take from it.")
    return "\n".join(lines)
