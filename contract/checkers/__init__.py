"""Task-agnostic checkers, by the name contract.toml uses (spec §5).

A clause naming a checker that is not here is a harness error, never a
clause failure. Every checker takes the Board positionally and its
arguments by keyword, and returns a Result.
"""
from contract.checkers.base import Result
from contract.checkers.geometry import decoupling_near, outline_within
from contract.checkers.identity import part_present

REGISTRY = {
    "outline_within": outline_within,
    "decoupling_near": decoupling_near,
    "part_present": part_present,
}

__all__ = ["REGISTRY", "Result"]
