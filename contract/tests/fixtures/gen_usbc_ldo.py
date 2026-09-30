"""Builds the usbc-ldo mutation fixtures (design 2026-09-29-contract-tasks §5).

One clean board satisfying every usbc-ldo clause; one mutant per clause, a
single edit that must fail that clause and no other; adversarial variants
`<clause>--<case>`, each a way the grader could be fooled. Pin names follow
KiCad's USB_C_Receptacle_USB2.0_16P and AP2112K-3.3 symbols; the header's
pins are `Pin_n`, as KiCad names them. Run in the engine image:

    ./etchwork contract fixtures
"""
import sys
from pathlib import Path

import pcbnew

from contract.tests.fixtures.fixture_lib import build as build_board, fp, mm, set_net, write_all

SIZE = (40.0, 30.0)

USB_C = {"A1": ("GND", "GND"), "A12": ("GND", "GND"), "B1": ("GND", "GND"),
         "B12": ("GND", "GND"), "A4": ("VBUS", "VBUS"), "A9": ("VBUS", "VBUS"),
         "B4": ("VBUS", "VBUS"), "B9": ("VBUS", "VBUS"), "A5": ("CC1", "CC1"),
         "B5": ("CC2", "CC2"), "A6": ("", "D+"), "B6": ("", "D+"), "A7": ("", "D-"),
         "B7": ("", "D-"), "A8": ("", "SBU1"), "B8": ("", "SBU2"), "SH": ("GND", "SHIELD")}

# ref -> (library, footprint, value, centre (mm), {pad number: (net, pin name)})
PARTS = {
    "J1": ("Connector_USB", "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
           "USB_C_Receptacle_USB2.0_16P", (8.0, 15.0), USB_C),
    "U1": ("Package_TO_SOT_SMD", "SOT-23-5", "AP2112K-3.3", (22.0, 15.0),
           {"1": ("VBUS", "VIN"), "2": ("GND", "GND"), "3": ("VBUS", "EN"),
            "4": ("", "NC"), "5": ("+3V3", "VOUT")}),
    "R1": ("Resistor_SMD", "R_0603_1608Metric", "5.1k", (6.0, 24.0),
           {"1": ("CC1", ""), "2": ("GND", "")}),
    "R2": ("Resistor_SMD", "R_0603_1608Metric", "5.1k", (10.0, 24.0),
           {"1": ("CC2", ""), "2": ("GND", "")}),
    "C1": ("Capacitor_SMD", "C_0603_1608Metric", "1u", (16.0, 10.0),
           {"1": ("GND", ""), "2": ("VBUS", "")}),
    "C2": ("Capacitor_SMD", "C_0603_1608Metric", "1u", (28.0, 10.0),
           {"1": ("+3V3", ""), "2": ("GND", "")}),
    "J2": ("Connector_PinHeader_2.54mm", "PinHeader_1x02_P2.54mm_Vertical", "Conn_01x02",
           (34.0, 13.0), {"1": ("+3V3", "Pin_1"), "2": ("GND", "Pin_2")}),
}

# Each capacitor's supply pad 1.2 mm from the regulator pin it serves.
NEAR = [("C1", "2", "U1", "1", -1.2), ("C2", "1", "U1", "5", 1.2)]


def build():
    return build_board(PARTS, SIZE, NEAR)


def _regulator_input_isolated(board):
    # The regulator's VIN and EN, and C1 with them, move to VIN_X, which
    # VBUS never reaches: the input is still decoupled (else `cin` fails
    # too), but unpowered.
    for ref, number in [("U1", "1"), ("U1", "3"), ("C1", "2")]:
        set_net(board, ref, number, "VIN_X")


def _output_on_vbus(board):
    # The regulator's output, its cap and the header all on VBUS: every
    # connection clause still holds, only the rails are one net.
    for ref, number in [("U1", "5"), ("C2", "1"), ("J2", "1")]:
        set_net(board, ref, number, "VBUS")


MUTANTS = {
    "usb_conn": lambda b: fp(b, "J1").SetFPID(
        pcbnew.LIB_ID("Connector_USB", "USB_Micro-B_Molex-105017-0001")),
    "ldo_rating": lambda b: fp(b, "U1").SetValue("MCP1700-3302E"),
    "cc1_rd": lambda b: fp(b, "R1").SetValue("10k"),
    "cc2_rd": lambda b: fp(b, "R2").SetValue("22k"),
    # CC2 joins CC1: R1 and R2 both terminate the one net.
    "cc_separate": lambda b: set_net(b, "J1", "B5", "CC1"),
    "vbus_to_ldo": _regulator_input_isolated,
    "ldo_out": lambda b: set_net(b, "J2", "1", "OUT_X"),
    "cin": lambda b: fp(b, "C1").SetPosition(mm(30.0, 26.0)),
    "cout": lambda b: fp(b, "C2").SetPosition(mm(30.0, 4.0)),
    "rails_distinct": _output_on_vbus,
}

# Adversarial variants: `<clause>--<case>`, each failing `<clause>` alone.
VARIANTS = {
    # A 250 mA part in the regulator's place.
    "ldo_rating--250ma": lambda b: fp(b, "U1").SetValue("HT7333-A"),
    # CC1 wired straight to ground: R1 is left on a CC1 net of its own.
    "cc1_rd--direct-to-gnd": lambda b: set_net(b, "J1", "A5", "GND"),
}


def main(out_dir) -> None:
    write_all(out_dir, build, {**MUTANTS, **VARIANTS})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "usbc-ldo")
