"""Forecaster — gradient-boosted onset prediction ("the lookout")."""

from .metrics import (
    EvaluationResult,
    ScenarioOutcome,
    evaluate,
    fixed_threshold_baseline,
    lead_time_histogram,
    scenario_outcomes,
)
from .serve import EVIDENCE_LABELS, Forecast, ForecasterService, RiskBands

__all__ = [
    "EVIDENCE_LABELS",
    "EvaluationResult",
    "Forecast",
    "ForecasterService",
    "RiskBands",
    "ScenarioOutcome",
    "evaluate",
    "fixed_threshold_baseline",
    "lead_time_histogram",
    "scenario_outcomes",
]
