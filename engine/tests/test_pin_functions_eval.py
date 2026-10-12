"""The pin-function evaluation covers real KiCad libraries,
six real netlists from earlier runs, and every single-pin miswiring of them.

Runs inside the image, where /usr/share/kicad/symbols exists.
"""
import re
import tomllib

import pytest

from circuit_mcp import kicad_lib, parts
from conftest import FIXTURES

PINFN = FIXTURES / "pinfn"
TABLE = tomllib.loads((parts._TABLE_PATH).read_text())


def findings(text: str) -> list[str]:
    return kicad_lib.function_mismatches(text, parts.pin_lookup)


def fixture(name: str) -> str:
    return (PINFN / name).read_text()


def real_netlist(refs: dict[str, tuple[str, str]], nets: dict[str, list[str]]) -> str:
    """A KiCad-shaped netlist for arbitrary real parts, in the unit tests'
    `netlist()` shape (comps with libsource, then nets) -- used for the final
    review's regression cases, which need real symbols outside the canned
    `PARTS` of test_pin_functions.py."""
    comps = "".join(
        f'(comp (ref "{ref}") (value "x") (libsource (lib "{lib}") (part "{part}")))\n'
        for ref, (lib, part) in refs.items())
    body = ""
    for code, (name, pins) in enumerate(nets.items(), start=1):
        nodes = "".join(f'(node (ref "{p.split(":")[0]}") (pin "{p.split(":")[1]}"))'
                        for p in pins)
        body += f'(net (code "{code}") (name "{name}") {nodes})\n'
    return f"(export (components\n{comps}) (nets\n{body}))"


# --- the table agrees with KiCad's own pin names ------------------------------

# The port each table row's pin must be, read from the datasheet figure the
# row cites. KiCad's symbol names the port, so a wrong pin number in the table
# fails here rather than on a board.
PORTS = {
    "MCU_Microchip_ATtiny:ATtiny25V-10S":
        {"1": "PB5", "2": "PB3", "3": "PB4", "5": "PB0", "6": "PB1", "7": "PB2"},
    "MCU_Microchip_ATtiny:ATtiny25V-10SS":
        {"1": "PB5", "2": "PB3", "3": "PB4", "5": "PB0", "6": "PB1", "7": "PB2"},
    "MCU_Microchip_ATtiny:ATtiny25V-10P":
        {"1": "PB5", "2": "PB3", "3": "PB4", "5": "PB0", "6": "PB1", "7": "PB2"},
    "MCU_Microchip_ATmega:ATmega48PV-10A":
        {"12": "PB0", "13": "PB1", "14": "PB2", "15": "PB3", "16": "PB4", "17": "PB5",
         "27": "PC4", "28": "PC5", "29": "PC6", "30": "PD0", "31": "PD1"},
    "MCU_Microchip_ATmega:ATmega48PV-10P":
        {"14": "PB0", "15": "PB1", "16": "PB2", "17": "PB3", "18": "PB4", "19": "PB5",
         "27": "PC4", "28": "PC5", "1": "PC6", "2": "PD0", "3": "PD1"},
}


def test_every_table_row_is_checked_against_a_port():
    assert set(TABLE) == set(PORTS)
    for key, row in TABLE.items():
        assert set(row["pins"]) == set(PORTS[key]), key


@pytest.mark.parametrize("key", sorted(PORTS))
def test_each_table_pin_is_the_port_kicad_names(key):
    library, symbol = key.split(":")
    found = parts.pin_lookup(library, symbol)
    assert found is not None, key
    for number, port in PORTS[key].items():
        assert port in kicad_lib.words(found.names[number]), (key, number, found.names[number])


# --- describe_part shows what the agent needs to wire by ----------------------

def _line(text: str, number: str) -> str:
    return next(l for l in text.splitlines() if re.match(rf"\s+{number}\s", l))


def test_describe_part_shows_the_isp_functions_of_an_inherited_attiny85():
    text = parts.describe_part("MCU_Microchip_ATtiny:ATtiny85-20S")
    assert "MOSI" in _line(text, "5")
    assert "MISO" in _line(text, "6")
    assert "SCK" in _line(text, "7")
    assert "RESET" in _line(text, "1")


def test_describe_part_shows_kicads_alternates_for_an_stm32():
    text = parts.describe_part("MCU_ST_STM32F1:STM32F103C8Tx")
    assert "functions:" in text and "SPI1_MOSI" in text


def test_describe_part_is_unchanged_for_a_part_with_no_functions():
    assert "functions:" not in parts.describe_part("Device:R")


# --- the real boards ----------------------------------------------------------

def test_the_stop1_board_is_caught_on_all_three_wrong_nets():
    found = findings(fixture("stop1.net"))
    assert [f.split(":")[0] for f in found] == ["ISP_MISO", "ISP_MOSI_RST", "ISP_SCK"]
    assert "SCK is U1:7 (PB2)" in next(f for f in found if f.startswith("ISP_SCK"))


@pytest.mark.parametrize("name", ["release.net", "release2.net", "release3.net",
                                  "test.net", "mycircuit2.net"])
def test_a_correct_board_has_no_findings(name):
    assert findings(fixture(name)) == []


# --- final-review regressions: two false-alarm classes on correct hobby
# designs, added after the pre-registered eval above (not pre-registered). ---

def test_a_bmp280s_spi_named_scl_pin_on_i2c_is_not_flagged():
    # Sensor_Pressure:BMP280 names pin 4 "SCK" (its SPI role). Wired to the
    # ATmega328P-A's SCL (pin 28) in I2C mode, that is correct wiring that
    # `describe_part` cannot show as anything but "SCK" -- only a connector's
    # pin names may demand, and BMP280 is not one.
    refs = {"U1": ("MCU_Microchip_ATmega", "ATmega328P-A"),
            "U2": ("Sensor_Pressure", "BMP280")}
    for net_name in ("SCL", "N$1"):
        text = real_netlist(refs, {net_name: ["U1:28", "U2:4"]})
        assert findings(text) == [], net_name


def test_an_attiny85_softserial_tx_net_is_not_flagged():
    # The ATtiny85 has no hardware UART at all, so a SoftwareSerial net named
    # TX (here on pin 3, wired to a generic header) is normal, not a mistake
    # no wiring could fix.
    refs = {"U1": ("MCU_Microchip_ATtiny", "ATtiny85-20S"),
            "J1": ("Connector_Generic", "Conn_01x03")}
    text = real_netlist(refs, {"TX": ["U1:3", "J1:1"]})
    assert findings(text) == []


# --- every single-pin miswiring ------------------------------------------------

def _move(text: str, net: str, ref: str, pin: str) -> str:
    """`text` with `ref`'s node on `net` moved to `pin`."""
    start = re.search(r'\(name "%s"\)' % re.escape(net), text).end()
    end = text.find("(net", start)
    end = len(text) if end == -1 else end
    block, count = re.subn(r'(\(ref "%s"\)\s*\(pin ")[^"]+(")' % re.escape(ref),
                           r"\g<1>%s\g<2>" % pin, text[start:end], count=1)
    assert count == 1, (net, ref)
    return text[:start] + block + text[end:]


# (fixture, MCU ref, {function net: the MCU pin it is correctly on})
BOARDS = [
    ("release.net", "U1", {"MISO": "6", "MOSI": "5", "RESET": "1", "SCK": "7"}),
    ("release2.net", "U2", {"MISO": "6", "MOSI": "5", "RESET": "1", "SCK": "7"}),
    ("release3.net", "U1", {"MISO": "6", "MOSI": "5", "RESET": "1", "SCK_BTN": "7"}),
    ("test.net", "U2", {"ISP_MISO": "6", "ISP_MOSI": "5", "ISP_RESET": "1", "ISP_SCK": "7"}),
]
NAMED = [(board, ref, net, pin)
         for board, ref, nets in BOARDS for net, right in nets.items()
         for pin in "12345678" if pin != right]


def test_the_named_mutation_set_is_the_pre_registered_112():
    assert len(NAMED) == 112


@pytest.mark.parametrize("board,ref,net,pin", NAMED)
def test_a_named_net_moved_to_any_other_pin_is_caught(board, ref, net, pin):
    found = findings(_move(fixture(board), net, ref, pin))
    assert any(f.startswith(f"{net}:") for f in found), found


# mycircuit2: unnamed nets from a named AVR-ISP-6 header to the ATmega328P-A.
ISP_NETS = {"N$7": "16", "N$8": "17", "N$9": "15"}
ATMEGA_PINS = sorted(parts.pin_lookup("MCU_Microchip_ATmega", "ATmega328P-A").names,
                     key=int)
PIN_NAMED = [(net, pin) for net, right in ISP_NETS.items()
             for pin in ATMEGA_PINS if pin != right]


def test_the_pin_name_mutation_set_is_the_pre_registered_93():
    assert len(ATMEGA_PINS) == 32
    assert len(PIN_NAMED) == 93


@pytest.mark.parametrize("net,pin", PIN_NAMED)
def test_a_named_header_pin_moved_to_any_other_mcu_pin_is_caught(net, pin):
    found = findings(_move(fixture("mycircuit2.net"), net, "U1", pin))
    assert any(f.startswith(f"{net}:") for f in found), found


# --- check_netlist gates on it ----------------------------------------------------

def test_check_netlist_fails_a_board_whose_declaration_shares_the_mistake(workspace):
    (workspace / "board.net").write_text(fixture("stop1.net"))
    (workspace / "expected.toml").write_text('[nets]\nISP_SCK = ["U1:2", "J2:3"]\n')
    out = parts.check_netlist("board.net", "expected.toml")
    assert out.startswith("FAIL -- board.net matches expected.toml, but its pin functions do not:")
    assert "Pin functions" in out and "SCK is U1:7 (PB2)" in out


def test_check_netlist_still_passes_a_correct_board(workspace):
    (workspace / "board.net").write_text(fixture("release.net"))
    (workspace / "expected.toml").write_text('[nets]\nSCK = ["J2:3", "U1:7"]\n')
    assert parts.check_netlist("board.net", "expected.toml").startswith("PASS")
