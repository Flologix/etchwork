"""The words each task's net specs match are the words KiCad's own symbols,
plus the engine's pin-function table, give a real board's pads. Fixtures
set pin names by hand; this is what ties those names to agent boards.
Engine image only."""
import pytest

pytest.importorskip("pcbnew")

from circuit_mcp import parts  # noqa: E402
from contract.match import words  # noqa: E402

# (library, symbol, pad number, a word a clause matches on that pad)
ROWS = [
    # usbc-ldo, usb-serial
    ("Connector", "USB_C_Receptacle_USB2.0_16P", "A4", "VBUS"),
    ("Connector", "USB_C_Receptacle_USB2.0_16P", "A5", "CC1"),
    ("Connector", "USB_C_Receptacle_USB2.0_16P", "B5", "CC2"),
    ("Connector", "USB_C_Receptacle_USB2.0_16P", "A1", "GND"),
    ("Regulator_Linear", "AP2112K-3.3", "1", "VIN"),
    ("Regulator_Linear", "AP2112K-3.3", "5", "VOUT"),
    ("Regulator_Linear", "AMS1117-3.3", "3", "VI"),
    ("Regulator_Linear", "AMS1117-3.3", "2", "VO"),
]


@pytest.mark.parametrize("library, symbol, number, word", ROWS)
def test_symbol_pin_carries_the_word(library, symbol, number, word):
    found = parts.pin_lookup(library, symbol)
    assert found is not None, f"{library}:{symbol} is not in the image's libraries"
    names = [found.names.get(number, ""), *found.functions.get(number, ())]
    assert word in frozenset().union(*(words(n) for n in names)), names
