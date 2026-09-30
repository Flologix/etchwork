"""Builds the keyfob-base mutation fixtures (design spec §6).

One clean board that satisfies every keyfob-base clause, and one mutant per
clause that differs from it by a single scripted edit and so must fail that
clause and no other. Adversarial variants, named `<clause>--<case>`, are
further single edits aimed at a way the grader could be fooled into a false
pass; each must fail its named clause and no other. Run in the engine image:

    ./etchwork contract fixtures

The encoder pinout here is this fixture's own, not a claim about any real
EV1527: pins are named through pad pin functions, which is one of the ways
the grader learns pin names. The OSC and switch nets have neutral names
(NET3, N_SW1, N_SW2), so those clauses resolve through pin names and not
net names. U2 is a stand-in 433 MHz transmitter on the same supply as the
encoder, so encoder clauses cannot be satisfied by whichever IC sits beside
it. The parts overlap (the coin cell would sit on the back of a real
fob); nothing here is DRC-checked, because CONTRACT does not grade DRC.
"""
import sys
from pathlib import Path

import pcbnew

from contract.tests.fixtures.fixture_lib import (build as build_board, fp, mm, outline, pad,
                                                 rename_net, set_net, write_all)

OUTLINE = (36.0, 22.0)

# ref -> (library, footprint, value, centre (mm), {pad number: (net, pin name)})
PARTS = {
    "BT1": ("Battery", "BatteryHolder_Keystone_3002_1x2032", "CR2032", (18.0, 11.0),
            {"1": ("VCC", "+"), "2": ("GND", "-")}),
    "U1": ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", "EV1527", (10.0, 6.0),
           {"1": ("GND", "VSS"), "2": ("NET3", "OSC"), "3": ("N_SW1", "K0"),
            "4": ("N_SW2", "K1"), "5": ("", "K2"), "6": ("", "K3"),
            "7": ("DOUT", "DOUT"), "8": ("VCC", "VDD")}),
    "R1": ("Resistor_SMD", "R_0603_1608Metric", "330k", (10.0, 12.0),
           {"1": ("NET3", ""), "2": ("VCC", "")}),
    "R2": ("Resistor_SMD", "R_0603_1608Metric", "1k", (22.0, 4.0),
           {"1": ("DOUT", ""), "2": ("LED_A", "")}),
    "D1": ("LED_SMD", "LED_0603_1608Metric", "RED", (26.0, 4.0),
           {"1": ("GND", "K"), "2": ("LED_A", "A")}),
    # The transmitter: pin names only, no data-input words (K0-K3/D0-D3).
    "U2": ("Package_TO_SOT_SMD", "SOT-23-6", "SYN115", (24.0, 10.0),
           {"1": ("", "ANT"), "2": ("GND", "VSS"), "3": ("", "XTAL"), "4": ("", "CLK"),
            "5": ("VCC", "VDD"), "6": ("DOUT", "DATA")}),
    "C1": ("Capacitor_SMD", "C_0603_1608Metric", "100n", (14.5, 4.1),
           {"1": ("VCC", ""), "2": ("GND", "")}),
    "SW1": ("Button_Switch_SMD", "Panasonic_EVQPUJ_EVQPUA", "SW_Push", (6.0, 17.0),
            {"1": ("VCC", ""), "2": ("N_SW1", "")}),
    "SW2": ("Button_Switch_SMD", "Panasonic_EVQPUJ_EVQPUA", "SW_Push", (30.0, 17.0),
            {"1": ("VCC", ""), "2": ("N_SW2", "")}),
}


def _cap_beside_transmitter(board):
    # C1's supply pad (1) 1.2 mm from U2:VDD, far from U1:VDD. The cap is
    # still across VCC and GND: only where it sits is wrong.
    vdd = pad(board, "U2", "5").GetPosition()
    c1 = fp(board, "C1")
    c1.SetPosition(pcbnew.VECTOR2I(vdd.x + pcbnew.FromMM(2.0), vdd.y))


def _isolate_encoder_supply(board):
    # U1:VDD and C1's supply pad move together onto VENC, which the cell
    # never reaches: the encoder is unpowered, but still decoupled (else this
    # would fail `decoupling` too). U2 stays on VCC, so any-IC binding of the
    # encoder supply would still have seen a powered VDD pin.
    set_net(board, "U1", "8", "VENC")
    set_net(board, "C1", "1", "VENC")


def _led_straight_off_dout(board):
    # D1's anode straight on DOUT, no resistor in series; the 1k R2 now runs
    # from DOUT to the transmitter's DATA input instead. R2 touches the LED's
    # net but does not limit its current.
    set_net(board, "D1", "2", "DOUT")
    set_net(board, "R2", "2", "TX_DATA")
    set_net(board, "U2", "6", "TX_DATA")


def _switch_on_auto_named_net(board):
    # The encoder's DOUT net carries a KiCad-style auto-name, Net-(D1-A),
    # whose words include D1 (a data-input synonym), and SW2 moves onto it
    # off its K1 net. No encoder data input is reached by SW2; only reading
    # the auto-name as function would say otherwise.
    rename_net(board, "DOUT", "Net-(D1-A)")
    set_net(board, "SW2", "2", "Net-(D1-A)")


def build():
    return build_board(PARTS, OUTLINE)


MUTANTS = {
    "envelope": lambda b: outline(b, 40.0, 22.0),
    "coincell": lambda b: fp(b, "BT1").SetFPID(
        pcbnew.LIB_ID("Battery", "BatteryHolder_Keystone_3000_1x12mm")),
    "encoder": lambda b: fp(b, "U1").SetValue("LM358"),
    "buttons": lambda b: fp(b, "SW2").SetFPID(pcbnew.LIB_ID(
        "Button_Switch_SMD", "SW_DIP_SPSTx01_Slide_6.7x4.1mm_W6.73mm_P2.54mm_LowProfile_JPin")),
    "led": lambda b: fp(b, "D1").SetFPID(pcbnew.LIB_ID("Diode_SMD", "D_0603_1608Metric")),
    "rosc": lambda b: fp(b, "R1").SetValue("100k"),
    "led_limit": lambda b: fp(b, "R2").SetValue("100"),
    "decoupling": lambda b: fp(b, "C1").SetPosition(mm(30.0, 10.0)),
    "btn_inputs": lambda b: set_net(b, "SW2", "2", "N_SW1"),
    "btn_not_shorted": lambda b: set_net(b, "U1", "5", "VCC"),
    "batt_supply": lambda b: set_net(b, "BT1", "1", "VBATX"),
}

# Adversarial variants: `<clause>--<case>`, each failing `<clause>` alone.
VARIANTS = {
    "decoupling--near-transmitter": _cap_beside_transmitter,
    "batt_supply--encoder-vdd-isolated": _isolate_encoder_supply,
    "led_limit--no-series-resistor": _led_straight_off_dout,
    "buttons--spring-contact": lambda b: fp(b, "SW2").SetFPID(
        pcbnew.LIB_ID("Connector", "SpringContact_Harwin_S1941-46R")),
    "btn_inputs--auto-named-net": _switch_on_auto_named_net,
}


def main(out_dir) -> None:
    write_all(out_dir, build, {**MUTANTS, **VARIANTS})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "keyfob")
