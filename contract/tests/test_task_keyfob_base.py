from pathlib import Path

from contract import score

TASK = Path(__file__).resolve().parents[1] / "tasks" / "keyfob-base"
IDS = ["envelope", "coincell", "encoder", "buttons", "led", "rosc", "led_limit",
       "decoupling", "btn_inputs", "btn_not_shorted", "batt_supply"]


def test_loads_eleven_clauses_with_no_errors():
    t = score.load_task(TASK)
    assert [c.id for c in t.clauses] == IDS
    assert [c.error for c in t.clauses] == [None] * 11


def test_six_checkers_used():
    assert {c.checker for c in score.load_task(TASK).clauses} == {
        "outline_within", "part_present", "series_part_between",
        "decoupling_near", "net_connects", "no_short"}


def test_prompt_withholds_the_numbers():
    prompt = (TASK / "prompt.md").read_text()
    for number in ["37", "23.4", "330", "300k", "430k", "470", "4.7k", "10n", "3 mm"]:
        assert number not in prompt, number


# --- allowlists and bindings, on hand-built boards ----------------------------

import pytest  # noqa: E402

from contract.board import Footprint  # noqa: E402
from contract.tests.boards import fp, make, pad  # noqa: E402

_CLAUSE = {c.id: c for c in score.load_task(TASK).clauses}


def _sw(lib_id):
    return Footprint("SW1", "SW_Push", lib_id, (0.0, 0.0), 0.0, (), 2)


@pytest.mark.parametrize("lib_id", [
    "Connector:SpringContact_Harwin_S1941-46R",
    "Connector_Phoenix_MC:PhoenixContact_MC_1,5_2-G-3.5_1x02_P3.50mm_Horizontal",
    "Button_Switch_SMD:SW_SPDT_PCM12",
    "Button_Switch_SMD:SW_CK_JS202011CQN_DPDT_Straight",
    "Button_Switch_SMD:SW_Push_2P1T_Toggle_CK_PVA1xxH1xxxxxxV2",
    "Button_Switch_SMD:SW_DIP_SPSTx01_Slide_6.7x4.1mm_W6.73mm_P2.54mm_LowProfile_JPin",
    # The KiCad 10 library's own name for the CK JS202011 DPDT slide switch.
    "Button_Switch_SMD:SW_DPDT_CK_JS202011JCQN",
])
def test_button_allowlist_rejects_non_momentary_and_connectors(lib_id):
    assert not _CLAUSE["buttons"].args["part"].matches(_sw(lib_id))


@pytest.mark.parametrize("lib_id", [
    "Button_Switch_SMD:Panasonic_EVQPUJ_EVQPUA",
    "Button_Switch_SMD:SW_SPST_PTS810",
    "Button_Switch_THT:SW_PUSH_6mm",
])
def test_button_allowlist_accepts_momentary_push_switches(lib_id):
    assert _CLAUSE["buttons"].args["part"].matches(_sw(lib_id))


def _two_ic_fob(c1_near="U1"):
    """U1 the encoder, U2 a transmitter also on VCC; C1 beside one of them."""
    c1 = (0.5, 0.0) if c1_near == "U2" else (20.5, 0.0)
    return make(
        footprints=[fp("U1", "EV1527", pad_count=8), fp("U2", "SYN115", pad_count=6),
                    fp("C1", "100n")],
        pads=[pad("U1", 8, "VCC", pos=(20.0, 0.0), names=["VDD"]),
              pad("U1", 1, "GND", names=["VSS"]), pad("U1", 3, "N_SW1", names=["K0"]),
              pad("U2", 5, "VCC", pos=(0.0, 0.0), names=["VDD"]),
              pad("U2", 2, "GND", names=["VSS"]), pad("U2", 6, "DOUT", names=["DATA"]),
              pad("C1", 1, "VCC", pos=c1), pad("C1", 2, "GND")])


def test_enc_aliases_bind_to_the_encoder_not_any_ic():
    from contract.match import resolve_pads
    vdd = _CLAUSE["decoupling"].args["pin"]
    assert [(p.ref, p.number) for p in resolve_pads(_two_ic_fob(), vdd)] == [("U1", "8")]


def test_decoupling_next_to_the_transmitter_does_not_count():
    c = _CLAUSE["decoupling"]
    from contract.checkers import REGISTRY
    assert REGISTRY[c.checker](_two_ic_fob("U1"), **c.args).passed
    assert not REGISTRY[c.checker](_two_ic_fob("U2"), **c.args).passed


def test_batt_supply_needs_the_encoder_powered_not_just_some_ic():
    from contract.checkers import REGISTRY
    c = _CLAUSE["batt_supply"]

    def fob(enc_net):
        return make(
            footprints=[fp("BT1", "CR2032"), fp("U1", "EV1527", pad_count=8),
                        fp("U2", "SYN115", pad_count=6)],
            pads=[pad("BT1", 1, "VCC", names=["+"]), pad("BT1", 2, "GND", names=["-"]),
                  pad("U1", 8, enc_net, names=["VDD"]), pad("U1", 3, "N_SW1", names=["K0"]),
                  pad("U2", 5, "VCC", names=["VDD"])])
    assert REGISTRY[c.checker](fob("VCC"), **c.args).passed
    assert not REGISTRY[c.checker](fob("VENC"), **c.args).passed
