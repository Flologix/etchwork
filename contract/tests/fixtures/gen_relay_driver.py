"""Builds the relay-driver mutation fixtures (design 2026-09-29-contract-tasks §5).

Pin names follow KiCad's AO3400A (G/S/D) and D (K/A) symbols; the relay's
pins are unnamed, as in KiCad's relay symbols, and the terminal's and
header's are `Pin_n`. The coil sits on pads 1 and 10 of the G5V-1
footprint: this fixture's own choice, and nothing reads it but the nets.
Run in the engine image:

    ./etchwork contract fixtures
"""
import sys
from pathlib import Path

import pcbnew

from contract.tests.fixtures.fixture_lib import build as build_board, fp, set_net, write_all

SIZE = (50.0, 35.0)

# ref -> (library, footprint, value, centre (mm), {pad number: (net, pin name)})
PARTS = {
    "J1": ("TerminalBlock_Phoenix", "TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal",
           "Screw_Terminal_01x02", (6.0, 6.0), {"1": ("+12V", "Pin_1"), "2": ("GND", "Pin_2")}),
    "J2": ("Connector_PinHeader_2.54mm", "PinHeader_1x03_P2.54mm_Vertical", "Conn_01x03",
           (6.0, 22.0), {"1": ("GND", "Pin_1"), "2": ("N_SIG", "Pin_2"),
                         "3": ("+3V3", "Pin_3")}),
    "Q1": ("Package_TO_SOT_SMD", "SOT-23", "AO3400A", (26.0, 26.0),
           {"1": ("N_GATE", "G"), "2": ("GND", "S"), "3": ("N_DRAIN", "D")}),
    "R1": ("Resistor_SMD", "R_0603_1608Metric", "100", (16.0, 24.5),
           {"1": ("N_SIG", ""), "2": ("N_GATE", "")}),
    "R2": ("Resistor_SMD", "R_0603_1608Metric", "100k", (22.0, 31.0),
           {"1": ("N_GATE", ""), "2": ("GND", "")}),
    "K1": ("Relay_THT", "Relay_SPDT_Omron_G5V-1", "G5V-1", (30.0, 4.0),
           {"1": ("+12V", ""), "10": ("N_DRAIN", "")}),
    "D1": ("Diode_SMD", "D_SOD-123", "1N4148W", (42.0, 12.0),
           {"1": ("+12V", "K"), "2": ("N_DRAIN", "A")}),
}


def build():
    return build_board(PARTS, SIZE)


def _clear_pin_names(board, ref):
    for p in fp(board, ref).Pads():
        p.SetPinFunction("")


MUTANTS = {
    "mosfet": lambda b: fp(b, "Q1").SetValue("2N3904"),
    "relay": lambda b: fp(b, "K1").SetFPID(
        pcbnew.LIB_ID("Connector_PinHeader_2.54mm", "PinHeader_2x03_P2.54mm_Vertical")),
    "coil_supply": lambda b: set_net(b, "K1", "1", "N_COIL"),
    "coil_drain": lambda b: set_net(b, "K1", "10", "N_COIL"),
    "source_gnd": lambda b: set_net(b, "Q1", "2", "N_SRC"),
    "flyback": lambda b: b.Remove(fp(b, "D1")),
    "gate_pulldown": lambda b: fp(b, "R2").SetValue("1k"),
    "gate_series": lambda b: fp(b, "R1").SetValue("10k"),
    "rails_distinct": lambda b: set_net(b, "J2", "3", "+12V"),
}

VARIANTS = {
    # The flyback diode turned round: it now shorts +12V through the MOSFET.
    "flyback--reversed": lambda b: (set_net(b, "D1", "1", "N_DRAIN"),
                                    set_net(b, "D1", "2", "+12V")),
    # A diode with no K/A pin names: its orientation cannot be read.
    "flyback--unnamed-pins": lambda b: _clear_pin_names(b, "D1"),
    # The control signal straight onto the gate; R1 is left off to one side.
    "gate_series--no-resistor": lambda b: set_net(b, "J2", "2", "N_GATE"),
}


def main(out_dir) -> None:
    write_all(out_dir, build, {**MUTANTS, **VARIANTS})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "relay-driver")
