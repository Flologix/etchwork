import pathlib
import pytest

from circuit_mcp import paths


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A throwaway /workspace for one test."""
    monkeypatch.setattr(paths, "WORKSPACE", tmp_path.resolve())
    return tmp_path.resolve()


FIXTURES = pathlib.Path(__file__).parent / "fixtures"
