"""Builds the mcu-i2c mutation fixtures (design 2026-09-29-contract-tasks §5).

Pin names follow KiCad's ATmega328P-A (via ATmega48PV-10A) and BME280
symbols. A real board's MCU pads carry several names (the symbol's `PC4`
plus the engine's pin-function `SDA`); a fixture pad carries one pin
function, so the fixture spells both in it (`SDA/PC4`). Run in the engine
image:

    ./etchwork contract fixtures
"""
import sys
from pathlib import Path

from contract.tests.fixtures.fixture_lib import (build as build_board, fp, mm, place, set_net,
                                                 write_all)

SIZE = (45.0, 35.0)

MCU = {"3": ("GND", "GND"), "4": ("+3V3", "VCC"), "5": ("GND", "GND"), "6": ("+3V3", "VCC"),
       "7": ("XT1", "XTAL1/PB6"), "8": ("XT2", "XTAL2/PB7"), "18": ("+3V3", "AVCC"),
       "20": ("", "AREF"), "21": ("GND", "GND"), "27": ("N_SDA", "SDA/PC4"),
       "28": ("N_SCL", "SCL/PC5"), "29": ("N_RST", "~{RESET}/PC6")}

BME280 = {"1": ("GND", "GND"), "2": ("+3V3", "CSB"), "3": ("N_SDA", "SDI"),
          "4": ("N_SCL", "SCK"), "5": ("GND", "SDO"), "6": ("+3V3", "VDDIO"),
          "7": ("GND", "GND"), "8": ("+3V3", "VDD")}

_CAP = ("Capacitor_SMD", "C_0603_1608Metric")
_RES = ("Resistor_SMD", "R_0603_1608Metric")

# ref -> (library, footprint, value, centre (mm), {pad number: (net, pin name)})
PARTS = {
    "J1": ("Connector_PinHeader_2.54mm", "PinHeader_1x02_P2.54mm_Vertical", "Conn_01x02",
           (40.0, 4.0), {"1": ("+3V3", "Pin_1"), "2": ("GND", "Pin_2")}),
    "U1": ("Package_QFP", "TQFP-32_7x7mm_P0.8mm", "ATmega328P-AU", (18.0, 17.0), MCU),
    "Y1": ("Crystal", "Crystal_HC49-4H_Vertical", "8MHz", (4.0, 26.0),
           {"1": ("XT1", ""), "2": ("XT2", "")}),
    "C1": (*_CAP, "22p", (4.0, 30.0), {"1": ("XT1", ""), "2": ("GND", "")}),
    "C2": (*_CAP, "22p", (9.0, 30.0), {"1": ("XT2", ""), "2": ("GND", "")}),
    "R1": (*_RES, "10k", (14.0, 6.0), {"1": ("N_RST", ""), "2": ("+3V3", "")}),
    "C3": (*_CAP, "100n", (8.0, 12.0), {"1": ("+3V3", ""), "2": ("GND", "")}),
    "C4": (*_CAP, "100n", (26.0, 24.0), {"1": ("+3V3", ""), "2": ("GND", "")}),
    "U2": ("Package_LGA", "Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering", "BME280",
           (34.0, 17.0), BME280),
    "C5": (*_CAP, "100n", (30.0, 24.0), {"1": ("+3V3", ""), "2": ("GND", "")}),
    "R2": (*_RES, "4.7k", (26.0, 8.0), {"1": ("N_SDA", ""), "2": ("+3V3", "")}),
    "R3": (*_RES, "4.7k", (30.0, 8.0), {"1": ("N_SCL", ""), "2": ("+3V3", "")}),
}

# C3 serves both VCC pins (4 and 6, 1.6 mm apart), C4 AVCC, C5 VDD and VDDIO.
NEAR = [("C3", "1", "U1", "4", -1.2), ("C4", "1", "U1", "18", 1.2),
        ("C5", "1", "U2", "8", -1.2)]


def build():
    return build_board(PARTS, SIZE, NEAR)


def _avcc_on_its_own_net(board):
    # AVCC and its cap move to AVCC_X, which the supply never reaches: AVCC
    # is still decoupled (else mcu_decoupling fails too), but unpowered.
    set_net(board, "U1", "18", "AVCC_X")
    set_net(board, "C4", "1", "AVCC_X")


def _sdo_via_resistor(board):
    # SDO leaves ground for a net of its own, pulled to +3V3 by R4: a pin
    # tied through a resistor is not tied directly.
    set_net(board, "U2", "5", "N_SDO")
    place(board, "R4", *_RES, "10k", (38.0, 24.0), {"1": ("N_SDO", ""), "2": ("+3V3", "")})


MUTANTS = {
    "mcu": lambda b: fp(b, "U1").SetValue("ATmega32U4-AU"),
    "sensor": lambda b: fp(b, "U2").SetValue("BMP280"),
    "crystal": lambda b: fp(b, "Y1").SetValue("16MHz"),
    "mcu_decoupling": lambda b: fp(b, "C3").SetPosition(mm(40.0, 30.0)),
    "avcc": _avcc_on_its_own_net,
    "reset_pullup": lambda b: fp(b, "R1").SetValue("1k"),
    "crystal_xtal1": lambda b: set_net(b, "Y1", "1", "XT_X"),
    "crystal_xtal2": lambda b: set_net(b, "Y1", "2", "XT_Y"),
    "xtal1_load": lambda b: fp(b, "C1").SetValue("100n"),
    "xtal2_load": lambda b: fp(b, "C2").SetValue("1n"),
    "sda_pullup": lambda b: fp(b, "R2").SetValue("47k"),
    "scl_pullup": lambda b: fp(b, "R3").SetValue("100"),
    "sda_wired": lambda b: set_net(b, "U2", "3", "N_SDA2"),
    "scl_wired": lambda b: set_net(b, "U2", "4", "N_SCL2"),
    "csb_high": lambda b: set_net(b, "U2", "2", "GND"),
    "sdo_tied": lambda b: set_net(b, "U2", "5", "N_SDO"),
    "sensor_decoupling": lambda b: fp(b, "C5").SetPosition(mm(26.0, 32.0)),
}

VARIANTS = {
    # The VCC pins keep their cap; AVCC loses its own.
    "mcu_decoupling--one-pin-only": lambda b: fp(b, "C4").SetPosition(mm(40.0, 30.0)),
    "sdo_tied--via-resistor-only": _sdo_via_resistor,
}


def main(out_dir) -> None:
    write_all(out_dir, build, {**MUTANTS, **VARIANTS})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "mcu-i2c")
