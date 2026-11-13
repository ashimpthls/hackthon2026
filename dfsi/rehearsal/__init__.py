"""Rehearsal room — policy simulator ("the practice drill")."""

from .simulator import (
    CANDIDATES,
    CANDIDATES_BY_KEY,
    PolicyCandidate,
    SimulationOutcome,
    run_drill,
    simulate,
)

__all__ = [
    "CANDIDATES",
    "CANDIDATES_BY_KEY",
    "PolicyCandidate",
    "SimulationOutcome",
    "run_drill",
    "simulate",
]
