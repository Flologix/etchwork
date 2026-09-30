"""Builds the usb-serial mutation fixtures (design 2026-09-29-contract-tasks §5).

Pin names follow KiCad's USB_C_Receptacle_USB2.0_16P and CH340C symbols; the
header's pins are `Pin_n`. The serial and reset nets have neutral names
(N_TX, N_RX, N_DTR, N_RST), so the clauses resolve them through pin names
and pad numbers, not net names. Run in the engine image:

    ./etchwork contract fixtures
"""
import sys
from pathlib import Path

from contract.tests.fixtures.fixture_lib import build as build_board, fp, mm, set_net, write_all

SIZE = (45.0, 30.0)

USB_C = {"A1": ("GND", "GND"), "A12": ("GND", "GND"), "B1": ("GND", "GND"),
         "B12": ("GND", "GND"), "A4": ("VBUS", "VBUS"), "A9": ("VBUS", "VBUS"),
         "B4": ("VBUS", "VBUS"), "B9": ("VBUS", "VBUS"), "A5": ("CC1", "CC1"),
         "B5": ("CC2", "CC2"), "A6": ("D+", "D+"), "B6": ("D+", "D+"), "A7": ("D-", "D-"),
         "B7": ("D-", "D-"), "A8": ("", "SBU1"), "B8": ("", "SBU2"), "SH": ("GND", "SHIELD")}

CH340C = {"1": ("GND", "GND"), "2": ("N_TX", "TXD"), "3": ("N_RX", "RXD"),
          "4": ("N_V3", "V3"), "5": ("D+", "UD+"), "6": ("D-", "UD-"), "7": ("", "NC"),
          "8": ("", "NC"), "9": ("", "~{CTS}"), "10": ("", "~{DSR}"), "11": ("", "~{RI}"),
          "12": ("", "~{DCD}"), "13": ("N_DTR", "~{DTR}"), "14": ("", "~{RTS}"),
          "15": ("", "R232"), "16": ("VBUS", "VCC")}

# ref -> (library, footprint, value, centre (mm), {pad number: (net, pin name)})
PARTS = {
    "J1": ("Connector_USB", "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
           "USB_C_Receptacle_USB2.0_16P", (6.0, 15.0), USB_C),
    "U1": ("Package_SO", "SOIC-16_3.9x9.9mm_P1.27mm", "CH340C", (22.0, 15.0), CH340C),
    "R1": ("Resistor_SMD", "R_0603_1608Metric", "5.1k", (6.0, 25.0),
           {"1": ("CC1", ""), "2": ("GND", "")}),
    "R2": ("Resistor_SMD", "R_0603_1608Metric", "5.1k", (10.0, 25.0),
           {"1": ("CC2", ""), "2": ("GND", "")}),
    "C1": ("Capacitor_SMD", "C_0603_1608Metric", "100n", (28.0, 6.0),
           {"1": ("VBUS", ""), "2": ("GND", "")}),
    "C2": ("Capacitor_SMD", "C_0603_1608Metric", "100n", (16.0, 6.0),
           {"1": ("N_V3", ""), "2": ("GND", "")}),
    "C3": ("Capacitor_SMD", "C_0603_1608Metric", "100n", (30.0, 26.0),
           {"1": ("N_DTR", ""), "2": ("N_RST", "")}),
    "J2": ("Connector_PinHeader_2.54mm", "PinHeader_1x06_P2.54mm_Vertical", "Conn_01x06",
           (40.0, 6.0), {"1": ("GND", "Pin_1"), "2": ("", "Pin_2"), "3": ("VBUS", "Pin_3"),
                         "4": ("N_TX", "Pin_4"), "5": ("N_RX", "Pin_5"),
                         "6": ("N_RST", "Pin_6")}),
}

NEAR = [("C1", "1", "U1", "16", 1.2), ("C2", "1", "U1", "4", -1.2)]


def build():
    return build_board(PARTS, SIZE, NEAR)


def _swap(board, ref, a, b, net_a, net_b):
    set_net(board, ref, a, net_b)
    set_net(board, ref, b, net_a)


def _v3_on_vbus(board):
    # V3 and its capacitor both on VBUS: C2 still decouples the pin (else
    # `v3_cap` fails too), but V3 is tied to the 5 V supply.
    set_net(board, "U1", "4", "VBUS")
    set_net(board, "C2", "1", "VBUS")


def _dp_dm_joined(board):
    # Every D-/UD- pad joins the D+ net: connectivity to the bridge holds
    # (dp, dm both still reach), only the pair is one net.
    for ref, number in [("J1", "A7"), ("J1", "B7"), ("U1", "6")]:
        set_net(board, ref, number, "D+")


MUTANTS = {
    "bridge": lambda b: fp(b, "U1").SetValue("CH340E"),
    "cc1_rd": lambda b: fp(b, "R1").SetValue("10k"),
    "cc2_rd": lambda b: fp(b, "R2").SetValue("22k"),
    "cc_separate": lambda b: set_net(b, "J1", "B5", "CC1"),
    "dp": lambda b: set_net(b, "U1", "5", "DP_X"),
    "dm": lambda b: set_net(b, "U1", "6", "DM_X"),
    "v3_cap": lambda b: fp(b, "C2").SetPosition(mm(6.0, 3.0)),
    "vcc_cap": lambda b: fp(b, "C1").SetPosition(mm(12.0, 3.0)),
    "tx_out": lambda b: set_net(b, "J2", "4", "N_X4"),
    "rx_in": lambda b: set_net(b, "J2", "5", "N_X5"),
    "dtr_reset": lambda b: fp(b, "C3").SetValue("1u"),
    "v3_not_vcc": _v3_on_vbus,
    # D- joins D+ on the receptacle's own pads (the bridge's UD- follows).
    "dp_dm_distinct": _dp_dm_joined,
    # RXD and header pin 5 join the TXD net: both connections still hold,
    # only the pair is one net.
    "tx_rx_distinct": lambda b: (set_net(b, "U1", "3", "N_TX"), set_net(b, "J2", "5", "N_TX")),
}

VARIANTS = {
    # D+ and D- crossed at the bridge.
    "dp--swapped": lambda b: _swap(b, "U1", "5", "6", "D+", "D-"),
    # TXD and RXD crossed: TXD reaches pin 5, RXD pin 4.
    "tx_out--crossed": lambda b: _swap(b, "U1", "2", "3", "N_TX", "N_RX"),
    # DTR wired straight to pin 6; C3 is left hanging off the reset net.
    "dtr_reset--no-capacitor": lambda b: set_net(b, "U1", "13", "N_RST"),
}

# Variants that fail more than their named clause.
EXPECT = {
    "dp--swapped": {"dp", "dm"},
    "tx_out--crossed": {"tx_out", "rx_in"},
}


def main(out_dir) -> None:
    write_all(out_dir, build, {**MUTANTS, **VARIANTS})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "usb-serial")
