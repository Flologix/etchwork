import pytest

from circuit_mcp import paths


def test_relative_paths_resolve_inside_the_workspace(workspace):
    assert paths.inside("board.kicad_pcb") == workspace / "board.kicad_pcb"
    assert paths.rel(workspace / "sub" / "x.net") == "sub/x.net"


def test_escapes_are_refused(workspace):
    for bad in ("../etc/passwd", "/etc/passwd", "sub/../../x"):
        with pytest.raises(ValueError):
            paths.inside(bad)


def test_symlinks_out_of_the_workspace_are_refused(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("x")
    (workspace / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        paths.inside("link.txt")


def test_library_roots_default_to_kicads():
    assert str(paths.SYMBOL_ROOT) == "/usr/share/kicad/symbols"
    assert str(paths.FP_ROOT) == "/usr/share/kicad/footprints"
