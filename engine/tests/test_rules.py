import json
import shutil

from circuit_mcp import rules, service
from conftest import FIXTURES


def _board(workspace):
    shutil.copy(FIXTURES / "blinker.net", workspace / "blinker.net")
    service.call("pcb", {"netlist": "blinker.net"})
    service.call("outline", {"board": "blinker.kicad_pcb", "width_mm": 36, "height_mm": 28})
    return "blinker.kicad_pcb"


def _usbc(workspace):
    """The first release check's own netlist: a USB-C receptacle whose VBUS
    pads are 0.6 mm wide, CC pads 0.3 mm, plus two NPTH locating holes."""
    shutil.copy(FIXTURES / "usbc.net", workspace / "usbc.net")
    service.call("pcb", {"netlist": "usbc.net"})
    service.call("outline", {"board": "usbc.kicad_pcb", "width_mm": 40, "height_mm": 30})
    return "usbc.kicad_pcb"


def _classes_on_board(workspace, board, *nets):
    code, out = service.python(
        "import json, sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "ns = b.GetDesignSettings().m_NetSettings\n"
        "res = {}\n"
        "for n in sys.argv[2:]:\n"
        "    name = b.FindNet(n).GetNetClassName()\n"
        "    res[n] = [name, round(pcbnew.ToMM(ns.GetNetClassByName(name).GetTrackWidth()), 4)]\n"
        "print(json.dumps(res))\n",
        str(workspace / board), *nets)
    assert code == 0, out
    return json.loads(next(line for line in out.splitlines() if line.startswith("{")))


def test_a_net_is_capped_at_its_smallest_copper_pad(workspace):
    """Release check 1: VBUS rated 2a_to_5a (2.5 mm) on a USB-C whose VBUS pads
    are 0.6 mm wide. Routing at 2.5 mm into a 0.6 mm pad is a clearance error
    at every pad; the cap routes the net at the pad's width instead, as its
    own class, and says so."""
    board = _usbc(workspace)
    (workspace / "usbc.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {"VBUS": {"netclass": "power_high_current_2a_to_5a", "width_mm": 2.5,
                          "clearance_mm": 0.2}},
        "needs_review": [],
    }))
    result = rules.apply_netclasses(board, "usbc.netclasses.json")
    assert result["capped"] == [["VBUS", 2.5, 0.6]]
    assert result["classes"] == ["power_high_current_2a_to_5a@0.6"]
    assert _classes_on_board(workspace, board, "VBUS") == {
        "VBUS": ["power_high_current_2a_to_5a@0.6", 0.6]}
    applied = json.loads((workspace / "usbc.netclasses.applied.json").read_text())
    assert applied == {"VBUS": 0.6}


def test_an_npth_pad_does_not_cap_a_net(workspace):
    """A mechanical hole carries no copper, so its size says nothing about how
    wide a track into the net can be."""
    board = _usbc(workspace)
    code, out = service.python(
        "import sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "fp = next(f for f in b.GetFootprints() if f.GetReference() == 'J1')\n"
        "hole = next(p for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH)\n"
        "hole.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.3), pcbnew.FromMM(0.3)))\n"
        "hole.SetNet(b.FindNet('VBUS'))\n"
        "pcbnew.SaveBoard(sys.argv[1], b)\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "print('npth on VBUS:', sum(1 for f in b.GetFootprints() for p in f.Pads()\n"
        "      if p.GetNetname() == 'VBUS' and p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH))\n",
        str(workspace / board))
    assert code == 0, out
    assert "npth on VBUS: 1" in out, "the NPTH pad must be on VBUS for this test to mean anything"
    (workspace / "usbc.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {"VBUS": {"netclass": "power_high_current_2a_to_5a", "width_mm": 2.5,
                          "clearance_mm": 0.2}},
        "needs_review": [],
    }))
    result = rules.apply_netclasses(board, "usbc.netclasses.json")
    assert result["capped"] == [["VBUS", 2.5, 0.6]]


def test_nets_sharing_a_class_keep_their_own_widths_when_one_is_capped(workspace):
    """The shared class used to take the first net's settings for every net in
    it. Now a capped net gets its own class and the other keeps the tier's."""
    board = _usbc(workspace)
    (workspace / "usbc.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {
            "CC1": {"netclass": "signal_0_5a_to_2a", "width_mm": 0.5, "clearance_mm": 0.2},
            "VBUS": {"netclass": "signal_0_5a_to_2a", "width_mm": 0.5, "clearance_mm": 0.2},
        },
        "needs_review": [],
    }))
    result = rules.apply_netclasses(board, "usbc.netclasses.json")
    assert result["capped"] == [["CC1", 0.5, 0.3]]
    assert result["assigned"] == 2
    assert _classes_on_board(workspace, board, "CC1", "VBUS") == {
        "CC1": ["signal_0_5a_to_2a@0.3", 0.3],
        "VBUS": ["signal_0_5a_to_2a", 0.5],
    }
    applied = json.loads((workspace / "usbc.netclasses.applied.json").read_text())
    assert applied == {"CC1": 0.3, "VBUS": 0.5}


def test_netclasses_are_written_into_the_board_and_dsn(workspace):
    board = _board(workspace)
    (workspace / "blinker.netclasses.json").write_text(json.dumps({
        "version": 1,
        "nets": {
            # 0.5 mm: under the blinker's smallest VBUS pad (0.6 mm), so the
            # pad cap leaves this net in the shared class.
            "VBUS": {"netclass": "power_high_current_0_5a_to_2a", "width_mm": 0.5, "clearance_mm": 0.3},
            "NOT_A_NET": {"netclass": "signal_lt_0_5a", "width_mm": 0.25, "clearance_mm": 0.2},
        },
        "needs_review": [],
    }))
    result = rules.apply_netclasses(board, "blinker.netclasses.json")
    assert result["assigned"] == 1
    assert result["missing"] == ["NOT_A_NET"]
    code, out = service.python(
        "import sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "print(b.FindNet('VBUS').GetNetClassName())\n"
        "pcbnew.ExportSpecctraDSN(b, sys.argv[2])\n",
        str(workspace / board), str(workspace / "check.dsn"))
    assert code == 0, out
    assert out.strip().splitlines()[-1] == "power_high_current_0_5a_to_2a"
    assert result["capped"] == []
    assert "(width 500)" in (workspace / "check.dsn").read_text()


def test_finish_widens_narrow_tracks_but_not_into_a_small_pad(workspace):
    board = _board(workspace)
    script = (
        "import sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "fp = next(f for f in b.GetFootprints() if f.GetReference() == 'U1')\n"
        "pad = fp.Pads()[0]\n"
        "t = pcbnew.PCB_TRACK(b)\n"
        "t.SetStart(pad.GetPosition()); t.SetEnd(pad.GetPosition() + pcbnew.VECTOR2I(pcbnew.FromMM(3), 0))\n"
        "t.SetWidth(pcbnew.FromMM(0.15)); t.SetLayer(pcbnew.F_Cu); t.SetNet(pad.GetNet())\n"
        "b.Add(t); pcbnew.SaveBoard(sys.argv[1], b)\n"
        "print(pad.GetNetname(), pcbnew.ToMM(min(pad.GetSize().x, pad.GetSize().y)))\n")
    code, out = service.python(script, str(workspace / board))
    assert code == 0, out
    net, pad_min = out.strip().splitlines()[-1].split()
    result = rules.finish(board)
    widened = {w[0]: w[2] for w in result["widened"]}
    assert net in widened
    # A pad-entry track widens to the net's width, capped at the pad's smaller side.
    assert widened[net] <= float(pad_min) + 1e-6
    assert widened[net] >= 0.2


def test_finish_makes_smd_pads_on_the_pour_net_solid(workspace):
    board = _board(workspace)
    result = rules.finish(board, pour_net="GND")
    assert result["solid"], "the blinker board has SMD pads on GND"


# --- solid_pads: the thermal repair's board edit -------------------------------

# J1 pin B1 on release check 2's board: the pad its DRC reported starved.
RELEASE2_B1 = "bf6607d6-b799-4804-a2f6-26daf5188e71"


def _connections(workspace, board):
    """`pad uuid -> zone connection` for every pad on the board."""
    code, out = service.python(
        "import json, sys, pcbnew\n"
        "b = pcbnew.LoadBoard(sys.argv[1])\n"
        "print(json.dumps({p.m_Uuid.AsString(): int(p.GetLocalZoneConnection())\n"
        "                  for fp in b.GetFootprints() for p in fp.Pads()}))\n",
        str(workspace / board))
    assert code == 0, out
    return json.loads(next(line for line in out.splitlines() if line.startswith("{")))


def test_solid_pads_changes_exactly_the_named_pad(workspace):
    shutil.copy(FIXTURES / "boards" / "release2.kicad_pcb", workspace / "board.kicad_pcb")
    before = _connections(workspace, "board.kicad_pcb")

    answer = rules.solid_pads("board.kicad_pcb", [RELEASE2_B1, "no-such-uuid"])

    after = _connections(workspace, "board.kicad_pcb")
    assert answer == {"changed": [RELEASE2_B1], "missing": ["no-such-uuid"]}
    assert [u for u in after if after[u] != before[u]] == [RELEASE2_B1]
    # ZONE_CONNECTION_FULL is 2 in KiCad 10's enum.
    assert after[RELEASE2_B1] == 2
