from contract.checkers import REGISTRY
from contract.checkers.identity import part_present
from contract.match import PartSpec
from contract.tests.boards import fp, make

BUTTON = PartSpec.from_toml("button", {"lib_id": ["(?i)^Button_Switch_SMD:(?!SW_DIP)"]})


def test_registered():
    assert REGISTRY["part_present"] is part_present


def test_enough_matches_pass():
    b = make(footprints=[fp("SW1", lib_id="Button_Switch_SMD:Panasonic_EVQPUJ_EVQPUA"),
                         fp("SW2", lib_id="Button_Switch_SMD:SW_SPST_PTS810")])
    r = part_present(b, part=BUTTON, count=2)
    assert r.passed
    assert r.measured == {"matched": ["SW1", "SW2"], "count": 2}


def test_too_few_matches_fail_and_list_the_board():
    b = make(footprints=[fp("SW1", lib_id="Button_Switch_SMD:Panasonic_EVQPUJ_EVQPUA"),
                         fp("SW2", "DIP", "Button_Switch_SMD:SW_DIP_SPSTx01_Slide_6.7x4.1mm")])
    r = part_present(b, part=BUTTON, count=2)
    assert not r.passed
    assert r.measured["count"] == 1
    assert "SW_DIP_SPSTx01" in r.detail  # says what the agent chose instead


def test_empty_board_fails():
    assert not part_present(make(), part=BUTTON).passed
