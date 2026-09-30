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
    # mcu-i2c
    ("MCU_Microchip_ATmega", "ATmega328P-A", "4", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "6", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "18", "AVCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "7", "XTAL1"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "8", "XTAL2"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "27", "SDA"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "28", "SCL"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "29", "RESET"),
    # mcu-i2c: every package symbol the `mcu` allowlist accepts. The QFN
    # symbols carry PC4/PC5 only; the engine's SDA/SCL cover TQFP and DIP.
    ("MCU_Microchip_ATmega", "ATmega328P-A", "27", "PC4"),
    ("MCU_Microchip_ATmega", "ATmega328P-A", "28", "PC5"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "7", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "20", "AVCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "9", "XTAL1"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "10", "XTAL2"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "1", "RESET"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "27", "SDA"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "28", "SCL"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "27", "PC4"),
    ("MCU_Microchip_ATmega", "ATmega328P-P", "28", "PC5"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "4", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "6", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "18", "AVCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "7", "XTAL1"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "8", "XTAL2"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "29", "RESET"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "27", "PC4"),
    ("MCU_Microchip_ATmega", "ATmega328P-M", "28", "PC5"),
    ("MCU_Microchip_ATmega", "ATmega328P-MM", "3", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-MM", "16", "AVCC"),
    ("MCU_Microchip_ATmega", "ATmega328P-MM", "5", "XTAL1"),
    ("MCU_Microchip_ATmega", "ATmega328P-MM", "6", "XTAL2"),
    ("MCU_Microchip_ATmega", "ATmega328P-MM", "25", "RESET"),
    ("MCU_Microchip_ATmega", "ATmega328P-MM", "23", "PC4"),
    ("MCU_Microchip_ATmega", "ATmega328P-MM", "24", "PC5"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "4", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "6", "VCC"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "18", "AVCC"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "7", "XTAL1"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "8", "XTAL2"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "29", "RESET"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "27", "PC4"),
    ("MCU_Microchip_ATmega", "ATmega328-M", "28", "PC5"),
    # usbc-ldo: the 1117 family
    ("Regulator_Linear", "LD1117S33TR_SOT223", "3", "VI"),
    ("Regulator_Linear", "LD1117S33TR_SOT223", "2", "VO"),
    ("Regulator_Linear", "NCP1117-3.3_SOT223", "3", "VI"),
    ("Regulator_Linear", "NCP1117-3.3_SOT223", "2", "VO"),
    ("Regulator_Linear", "AP7361C-33E", "1", "VI"),
    ("Regulator_Linear", "AP7361C-33E", "3", "VO"),
    ("Regulator_Linear", "LM1117S-3.3", "3", "VI"),
    ("Regulator_Linear", "LM1117S-3.3", "2", "VO"),
    # usb-serial: the CH340K
    ("Interface_USB", "CH340K", "1", "UD+"),
    ("Interface_USB", "CH340K", "2", "UD"),
    ("Interface_USB", "CH340K", "10", "V3"),
    ("Interface_USB", "CH340K", "7", "VCC"),
    ("Interface_USB", "CH340K", "8", "TXD"),
    ("Interface_USB", "CH340K", "9", "RXD"),
    # relay-driver: the other pin order
    ("Transistor_FET", "Q_NMOS_GSD", "1", "G"),
    ("Transistor_FET", "Q_NMOS_GSD", "2", "S"),
    ("Transistor_FET", "Q_NMOS_GSD", "3", "D"),
    ("Sensor", "BME280", "2", "CSB"),
    ("Sensor", "BME280", "3", "SDI"),
    ("Sensor", "BME280", "4", "SCK"),
    ("Sensor", "BME280", "5", "SDO"),
    ("Sensor", "BME280", "6", "VDDIO"),
    ("Sensor", "BME280", "8", "VDD"),
]


@pytest.mark.parametrize("library, symbol, number, word", ROWS)
def test_symbol_pin_carries_the_word(library, symbol, number, word):
    found = parts.pin_lookup(library, symbol)
    assert found is not None, f"{library}:{symbol} is not in the image's libraries"
    names = [found.names.get(number, ""), *found.functions.get(number, ())]
    assert word in frozenset().union(*(words(n) for n in names)), names
