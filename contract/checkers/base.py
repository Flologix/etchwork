from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Result:
    passed: bool
    detail: str
    # What was actually found, in numbers where there are numbers. "outline is
    # 38.1 x 23.0 mm" rather than "fail" is what makes a failure actionable
    # (design spec §5, goal 2). Must be JSON-serialisable.
    measured: Any = None
