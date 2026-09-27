from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"

LIMITS = (
    "CONTRACT measures conformance of a delivered board to a written clause list "
    "derived from one real design, checked statically. It does not measure that the "
    "board works. It does not simulate. It does not cover RF. It does not check "
    "firmware, part availability, thermal or electrical margin, or EMC. A board that "
    "passes every clause is not a validated product, and a clause that fails may "
    "indicate a defensible alternative design rather than an error."
)


def test_readme_carries_the_stated_limits_verbatim():
    text = " ".join(README.read_text().split())
    assert LIMITS in text
