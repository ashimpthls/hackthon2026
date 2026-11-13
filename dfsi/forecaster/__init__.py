"""Forecaster — gradient-boosted onset prediction ("the lookout")."""

from .metrics import (
    EvaluationResult,
    ScenarioOutcome,
    evaluate,
    fixed_threshold_baseline,
    lead_time_histogram,
    scenario_outcomes,
)
from .serve import EVIDENCE_LABELS, Forecast, ForecasterService, risk_band

__all__ = [
    "EVIDENCE_LABELS",
    "EvaluationResult",
    "Forecast",
    "ForecasterService",
    "ScenarioOutcome",
    "evaluate",
    "fixed_threshold_baseline",
    "lead_time_histogram",
    "risk_band",
    "scenario_outcomes",
]
