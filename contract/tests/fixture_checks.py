"""Helpers shared by the fixture tests (test_keyfob.py, test_task_fixtures.py)."""


def canonical(data: dict) -> dict:
    # pcbnew.SaveBoard writes footprints (and their pads) sorted by each
    # item's own random UUID, so the same script run twice produces the
    # same content in a different order. UUIDs never reach this dump, so
    # sort by the fields that do -- ref for footprints, (ref, number) for
    # pads -- and compare content, not incidental file order.
    return {
        **data,
        "footprints": sorted(data["footprints"], key=lambda f: f["ref"]),
        "pads": sorted(data["pads"], key=lambda p: (p["ref"], p["number"])),
    }
