"""Task-agnostic checkers, by the name contract.toml uses (spec §5).

A clause naming a checker that is not here is a harness error, never a
clause failure. Every checker takes the Board positionally and its
arguments by keyword, and returns a Result.
"""
from contract.checkers.base import Result
from contract.checkers.geometry import decoupling_near, outline_within
from contract.checkers.identity import part_present
from contract.checkers.topology import diode_across, net_connects, no_short, series_part_between

REGISTRY = {
    "outline_within": outline_within,
    "decoupling_near": decoupling_near,
    "part_present": part_present,
    "series_part_between": series_part_between,
    "net_connects": net_connects,
    "no_short": no_short,
    "diode_across": diode_across,
}

__all__ = ["REGISTRY", "Result"]
