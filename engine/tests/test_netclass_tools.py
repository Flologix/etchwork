"""The two MCP tools the harness's model uses to classify nets (spec §4.2)."""
import json
import shutil

from circuit_mcp import netclass, tools
from conftest import FIXTURES

CLS = FIXTURES / "classify"


def _ws(workspace):
    shutil.copy(CLS / "cnc_xy_controller.net", workspace / "board.net")
    return "board.net"


def _all_open(net):
    out, offset = [], 0
    while offset is not None:
        page = json.loads(tools.net_evidence(net, offset=offset))
        out += page["open"]
        offset = page["next_offset"]
    return out, page


def test_evidence_pages_cover_every_open_net_once(workspace):
    net = _ws(workspace)
    opened, last = _all_open(net)
    names = [o["net"] for o in opened]
    assert len(names) == len(set(names)) == last["total_open"]
    assert set(last["menu"]["classes"]) >= {"ground", "signal"}
    assert all(o["evidence"] for o in opened)
    first = json.loads(tools.net_evidence(net))
    assert first["decided"] and all(d["source"].startswith("rule:") for d in first["decided"])
    assert not (workspace / "board.netclasses.json").exists()


def test_label_picks_are_written(workspace):
    net = _ws(workspace)
    labels = netclass.load_labels((CLS / "cnc_xy_controller.labels.toml").read_text())
    opened, _ = _all_open(net)
    picks = [{"net": o["net"], "class": labels[o["net"]][0], "tier": labels[o["net"]][1], "reason": "label"}
             for o in opened]
    out = tools.set_net_classes(net, picks)
    assert out.startswith("WROTE board.netclasses.json"), out
    data = json.loads((workspace / "board.netclasses.json").read_text())
    assert data["version"] == 1 and len(data["nets"]) == 53


def test_a_refusal_writes_nothing_and_names_every_problem(workspace):
    net = _ws(workspace)
    opened, _ = _all_open(net)
    picks = [{"net": o["net"], "class": "signal", "tier": "lt_0_5a", "reason": "guess"} for o in opened[1:]]
    out = tools.set_net_classes(net, picks)
    assert out.startswith("REFUSED:"), out
    assert "missing" in out and "power pin" in out
    assert not (workspace / "board.netclasses.json").exists()


def test_paths_outside_the_workspace_are_refused(workspace):
    assert "outside" in tools.net_evidence("../x.net") or "absolute" in tools.net_evidence("/etc/x.net")


def test_net_evidence_clamps_a_negative_offset_to_zero(workspace):
    # m-c: a negative offset must not be handed straight to list slicing --
    # open_nets[-1:-1+limit] silently reads from the end instead of refusing
    # or clamping, which would page over the same nets net_evidence(0) does,
    # or skip some, without any error to say so.
    net = _ws(workspace)
    normal = json.loads(tools.net_evidence(net, offset=0, limit=5))
    negative = json.loads(tools.net_evidence(net, offset=-3, limit=5))
    assert negative["open"] == normal["open"]


def test_net_evidence_clamps_limit_to_the_1_to_100_range(workspace):
    net = _ws(workspace)
    zero = json.loads(tools.net_evidence(net, limit=0))
    assert len(zero["open"]) == 1
    big = json.loads(tools.net_evidence(net, limit=500))
    assert len(big["open"]) == min(100, big["total_open"])
