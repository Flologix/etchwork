"""The one error that means the grader failed, not the board."""


class HarnessError(Exception):
    """The harness could not grade: a broken task file, an unreadable
    delivery, a dump that crashed. Reported apart from clause failures
    (design spec §7) and never counted against the agent."""
