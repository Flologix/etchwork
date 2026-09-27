"""Builds the keyfob-base mutation fixtures (design spec §6).

One clean board that satisfies every keyfob-base clause, and one mutant per
clause that differs from it by a single scripted edit and so must fail that
clause and no other. Run in the engine image:

    ./etchwork contract fixtures

The encoder pinout here is this fixture's own, not a claim about any real
EV1527: pins are named through pad pin functions, which is one of the ways
the grader learns pin names. The OSC and switch nets have neutral names
(NET3, N_SW1, N_SW2), so those clauses resolve through pin names and not
net names. The parts overlap (the coin cell would sit on the back of a real
fob); nothing here is DRC-checked, because CONTRACT does not grade DRC.
"""
import sys
from pathlib import Path

import pcbnew

FP_ROOT = Path("/usr/share/kicad/footprints")
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
    "C1": ("Capacitor_SMD", "C_0603_1608Metric", "100n", (14.5, 4.1),
           {"1": ("VCC", ""), "2": ("GND", "")}),
    "SW1": ("Button_Switch_SMD", "Panasonic_EVQPUJ_EVQPUA", "SW_Push", (6.0, 17.0),
            {"1": ("VCC", ""), "2": ("N_SW1", "")}),
    "SW2": ("Button_Switch_SMD", "Panasonic_EVQPUJ_EVQPUA", "SW_Push", (30.0, 17.0),
            {"1": ("VCC", ""), "2": ("N_SW2", "")}),
}


def _mm(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def _net(board, name):
    found = board.FindNet(name)
    if found is None:
        found = pcbnew.NETINFO_ITEM(board, name)
        board.Add(found)
    return found


def _outline(board, w, h):
    for item in list(board.GetDrawings()):
        if item.GetLayer() == pcbnew.Edge_Cuts:
            board.Remove(item)
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(_mm(*a))
        seg.SetEnd(_mm(*b))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(0.1))
        board.Add(seg)


def _fp(board, ref):
    return next(f for f in board.GetFootprints() if f.GetReference() == ref)


def _set_net(board, ref, number, net):
    for pad in _fp(board, ref).Pads():
        if pad.GetNumber() == number:
            pad.SetNet(_net(board, net))


def build():
    board = pcbnew.BOARD()
    _outline(board, *OUTLINE)
    for ref, (lib, name, value, (x, y), pins) in PARTS.items():
        fp = pcbnew.FootprintLoad(str(FP_ROOT / f"{lib}.pretty"), name)
        if fp is None:
            sys.exit(f"footprint {lib}:{name} is not in {FP_ROOT}")
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(ref)
        fp.SetValue(value)
        board.Add(fp)
        fp.SetPosition(_mm(x, y))
        for pad in fp.Pads():
            net, pin = pins[pad.GetNumber()]
            if net:
                pad.SetNet(_net(board, net))
            if pin:
                pad.SetPinFunction(pin)
    return board


MUTANTS = {
    "envelope": lambda b: _outline(b, 40.0, 22.0),
    "coincell": lambda b: _fp(b, "BT1").SetFPID(
        pcbnew.LIB_ID("Battery", "BatteryHolder_Keystone_3000_1x12mm")),
    "encoder": lambda b: _fp(b, "U1").SetValue("LM358"),
    "buttons": lambda b: _fp(b, "SW2").SetFPID(pcbnew.LIB_ID(
        "Button_Switch_SMD", "SW_DIP_SPSTx01_Slide_6.7x4.1mm_W6.73mm_P2.54mm_LowProfile_JPin")),
    "led": lambda b: _fp(b, "D1").SetFPID(pcbnew.LIB_ID("Diode_SMD", "D_0603_1608Metric")),
    "rosc": lambda b: _fp(b, "R1").SetValue("100k"),
    "led_limit": lambda b: _fp(b, "R2").SetValue("100"),
    "decoupling": lambda b: _fp(b, "C1").SetPosition(_mm(30.0, 10.0)),
    "btn_inputs": lambda b: _set_net(b, "SW2", "2", "N_SW1"),
    "btn_not_shorted": lambda b: _set_net(b, "U1", "5", "VCC"),
    "batt_supply": lambda b: _set_net(b, "BT1", "1", "VBATX"),
}


def main(out_dir) -> None:
    out_dir = Path(out_dir)
    for name in ["clean", *MUTANTS]:
        board = build()
        if name != "clean":
            MUTANTS[name](board)
        path = out_dir / name / "kicad" / "board.kicad_pcb"
        path.parent.mkdir(parents=True, exist_ok=True)
        pcbnew.SaveBoard(str(path), board)
        print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "keyfob")
