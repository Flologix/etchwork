"""The pin-function check, pure: netlist text and a canned lookup in, findings out.

Spec: docs/superpowers/specs/2026-09-24-circuit-pin-functions-design.md §3.3.
The real-library, real-netlist eval is test_pin_functions_eval.py.
"""
from circuit_mcp import kicad_lib
from circuit_mcp.kicad_lib import PartPins

# An 8-pin MCU shaped like the ATtiny85, and the parts around it. Pin 9 does
# not exist on a real ATtiny85; it is a synthetic pin, carrying SCL but not
# SCK, so a test can wire a sensor's SCK-named pin to an SCL-only pin without
# reusing pin 7 (which correctly carries both and other tests depend on that).
MCU = PartPins(
    names={"1": "~{RESET}/PB5", "2": "XTAL1/PB3", "3": "XTAL2/PB4", "4": "GND",
           "5": "AREF/PB0", "6": "PB1", "7": "PB2", "8": "VCC", "9": "PB6"},
    functions={"1": ("RESET",), "3": ("RXD",), "5": ("MOSI", "SDA"),
               "6": ("MISO",), "7": ("SCK", "SCL"), "9": ("SCL",)})
# A second MCU with no UART pin at all, unlike MCU above (which has RXD on
# pin 3) -- the ATtiny85's actual shape: no hardware UART anywhere.
MCU_NO_UART = PartPins(
    names={"1": "PB0", "2": "PB1", "3": "PB2", "4": "GND"},
    functions={"1": ("MOSI", "SCK"), "2": ("MISO",)})
ISP = PartPins(names={"1": "MISO", "2": "VCC", "3": "SCK", "4": "MOSI",
                      "5": "~{RST}", "6": "GND"}, functions={}, connector=True)
HEADER = PartPins(names={str(n): f"Pin_{n}" for n in range(1, 7)}, functions={},
                  connector=True)
RES = PartPins(names={"1": "~", "2": "~"}, functions={})
GPS = PartPins(names={"1": "VCC", "2": "GND", "3": "TXD", "4": "RXD"}, functions={})
DRIVER = PartPins(names={"1": "~{RESET}", "2": "STEP"}, functions={})
# A non-connector sensor named by SPI role -- BMP280's shape: pin 4 is "SCK"
# and correctly carries SCL in I2C mode. It has no known functions, so it is
# not a provider, and (fix under test) its pin names must not demand.
SENSOR = PartPins(names={"3": "SDI", "4": "SCK"}, functions={})

PARTS = {("MCU_Test", "MCU"): MCU, ("MCU_Test", "MCU_NO_UART"): MCU_NO_UART,
         ("Connector", "AVR-ISP-6"): ISP, ("Connector", "Conn_01x06"): HEADER,
         ("Device", "R"): RES, ("Module", "GPS"): GPS, ("Driver", "DRV"): DRIVER,
         ("Sensor", "SPI_I2C"): SENSOR}
REFS = {"U1": ("MCU_Test", "MCU"), "J1": ("Connector", "AVR-ISP-6"),
        "J2": ("Connector", "Conn_01x06"), "R1": ("Device", "R"),
        "M1": ("Module", "GPS"), "U2": ("Driver", "DRV"), "U3": ("Unknown", "Chip"),
        "U2x": ("MCU_Test", "MCU_NO_UART"), "S1": ("Sensor", "SPI_I2C")}


def lookup(library, symbol):
    return PARTS.get((library, symbol))


def netlist(nets: dict[str, list[str]]) -> str:
    """A KiCad-shaped netlist: every part in REFS, then the nets given."""
    comps = "".join(
        f'(comp (ref "{ref}") (value "x") (libsource (lib "{lib}") (part "{part}")))\n'
        for ref, (lib, part) in REFS.items())
    body = ""
    for code, (name, pins) in enumerate(nets.items(), start=1):
        nodes = "".join(f'(node (ref "{p.split(":")[0]}") (pin "{p.split(":")[1]}"))'
                        for p in pins)
        body += f'(net (code "{code}") (name "{name}") {nodes})\n'
    return f"(export (components\n{comps}) (nets\n{body}))"


def check(nets):
    return kicad_lib.function_mismatches(netlist(nets), lookup)


# --- words ------------------------------------------------------------------

def test_words_split_on_separators_and_resolve_aliases():
    assert kicad_lib.words("~{RESET}/PB5") == {"RESET", "PB5"}
    assert kicad_lib.words("SPI1_MOSI") == {"SPI1", "MOSI"}
    assert kicad_lib.words("ISP_MOSI_RST") == {"ISP", "MOSI", "RESET"}
    assert kicad_lib.words("sclk") == {"SCK"}
    assert kicad_lib.words("TXD") == {kicad_lib.UART}


def test_a_word_inside_a_longer_word_is_not_a_function():
    assert kicad_lib.words("TXLED") == {"TXLED"}
    assert kicad_lib.words("UART_TO_PC") == {"UART", "TO", "PC"}


# --- the netlist's parts ------------------------------------------------------

def test_libsources_map_each_ref_to_its_symbol():
    assert kicad_lib.read_libsources(netlist({}))["U1"] == ("MCU_Test", "MCU")


# --- the check ----------------------------------------------------------------

def test_a_bus_word_on_the_wrong_pin_is_found_and_the_right_pin_named():
    found = check({"ISP_SCK": ["U1:2", "J2:3"]})
    assert len(found) == 1
    assert found[0].startswith("ISP_SCK: the net's name says SCK")
    assert "SCK is U1:7 (PB2)" in found[0]


def test_a_bus_word_on_the_right_pin_passes():
    assert check({"ISP_SCK": ["U1:7", "J2:3"], "MOSI": ["U1:5", "J2:4"]}) == []


def test_every_word_in_a_name_must_be_carried():
    # stop1's net: RESET is on pin 1, MOSI is not.
    found = check({"ISP_MOSI_RST": ["U1:1", "J2:4", "J2:5"]})
    assert len(found) == 1 and "MOSI is U1:5 (AREF/PB0)" in found[0]


def test_a_named_header_pin_demands_its_bus_word_on_an_unnamed_net():
    found = check({"N$9": ["J1:4", "U1:2"]})
    assert len(found) == 1
    assert found[0].startswith("N$9: J1:4 is named MOSI")


def test_a_named_header_on_the_right_pin_passes():
    assert check({"N$7": ["J1:1", "U1:6"], "N$9": ["J1:4", "U1:5"]}) == []


def test_a_uart_name_from_the_other_end_passes():
    # The GPS module's TX goes to the MCU's RX: correct, and named for the GPS.
    assert check({"GPS_TX": ["M1:3", "U1:3"]}) == []


def test_a_uart_name_with_no_uart_pin_on_the_net_is_found():
    found = check({"GPS_TX": ["J2:1", "U1:2"]})
    assert len(found) == 1 and "TX or RX" in found[0]


def test_a_uart_name_on_a_part_with_no_uart_pin_at_all_is_not_a_demand():
    # MCU_NO_UART has no TX/RX anywhere -- an ATtiny85 SoftwareSerial TX net
    # is normal, unlike the case above, whose MCU has RXD on pin 3.
    assert check({"TX": ["U2x:3", "J2:1"]}) == []


def test_a_reset_name_on_a_drivers_reset_pin_passes():
    # A driver's RESET is legitimately driven by any GPIO.
    assert check({"DRV_RST": ["U2:1", "U1:2"]}) == []


def test_a_reset_name_with_no_reset_pin_on_the_net_is_found():
    found = check({"ISP_RESET": ["J2:5", "R1:2", "U1:5"]})
    assert len(found) == 1 and "RESET is U1:1 (~{RESET}/PB5)" in found[0]


def test_a_programming_headers_reset_pin_must_reach_the_mcus_reset():
    # A connector passes the signal through: its ~{RST} is the MCU's RESET.
    found = check({"RES_N": ["J1:5", "U1:2"]})
    assert len(found) == 1 and found[0].startswith("RES_N: J1:5 is named RESET")
    assert check({"RES_N": ["J1:5", "U1:1"]}) == []


def test_a_connectors_own_pin_never_satisfies_a_name():
    # release.net's shape: a net named RESET through a named ISP header. The
    # header's ~{RST} is not evidence the MCU pin is right.
    found = check({"RESET": ["J1:5", "R1:2", "U1:2"]})
    assert found and all("RESET is U1:1 (~{RESET}/PB5)" in f for f in found)


def test_a_sensors_spi_named_pin_on_i2c_is_not_a_demand():
    # BMP280's shape: pin 4 is named SCK (its SPI role) and is correctly
    # wired to the MCU's SCL in I2C mode. A non-connector part's pin names
    # must not demand anything -- only connectors do.
    assert check({"N$2": ["S1:4", "U1:7"]}) == []
    # Same, wired to a pin that carries SCL but not SCK (pin 9): still not a
    # false alarm, because the sensor's "SCK" pin name is not a demand.
    assert check({"N$3": ["S1:4", "U1:9"]}) == []
    # A named net still demands its own word, satisfied by the MCU's SCL pin
    # regardless of what the sensor's pin happens to be named.
    assert check({"SCL": ["U1:7", "S1:4"]}) == []


def test_a_net_with_no_known_mcu_gets_no_verdict():
    assert check({"MOSI": ["J1:4", "R1:1"]}) == []
    assert check({"MOSI": ["U3:1", "J2:4"]}) == []


def test_a_longer_word_is_not_a_demand():
    assert check({"TXLED": ["U1:2", "R1:1"]}) == []


def test_findings_are_one_per_missing_word():
    found = check({"ISP_SCK": ["U1:2"], "ISP_MISO": ["U1:3"], "SCK_BTN": ["U1:7"]})
    assert [f.split(":")[0] for f in found] == ["ISP_MISO", "ISP_SCK"]
