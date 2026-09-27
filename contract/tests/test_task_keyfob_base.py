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
