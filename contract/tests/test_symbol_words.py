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
    ("Regulator_Linear", "TLV75733PDBV", "1", "IN"),
    ("Regulator_Linear", "TLV75733PDBV", "5", "OUT"),
    ("Regulator_Linear", "XC6220B331MR", "1", "VIN"),
    ("Regulator_Linear", "XC6220B331MR", "5", "VOUT"),
    # usb-serial
    ("Connector", "USB_C_Receptacle_USB2.0_16P", "A6", "D+"),
    ("Connector", "USB_C_Receptacle_USB2.0_16P", "A7", "D"),
    ("Interface_USB", "CH340C", "2", "TXD"),
    ("Interface_USB", "CH340C", "3", "RXD"),
    ("Interface_USB", "CH340C", "4", "V3"),
    ("Interface_USB", "CH340C", "5", "UD+"),
    ("Interface_USB", "CH340C", "6", "UD"),
    ("Interface_USB", "CH340C", "13", "DTR"),
    ("Interface_USB", "CH340C", "16", "VCC"),
    ("Interface_USB", "CH340K", "4", "DTR"),
    # relay-driver
    ("Transistor_FET", "AO3400A", "1", "G"),
    ("Transistor_FET", "AO3400A", "2", "S"),
    ("Transistor_FET", "AO3400A", "3", "D"),
    ("Device", "D", "1", "K"),
    ("Device", "D", "2", "A"),
]


@pytest.mark.parametrize("library, symbol, number, word", ROWS)
def test_symbol_pin_carries_the_word(library, symbol, number, word):
    found = parts.pin_lookup(library, symbol)
    assert found is not None, f"{library}:{symbol} is not in the image's libraries"
    names = [found.names.get(number, ""), *found.functions.get(number, ())]
    assert word in frozenset().union(*(words(n) for n in names)), names
