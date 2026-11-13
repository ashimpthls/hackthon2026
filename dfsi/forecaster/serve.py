"""Forecaster — live scoring for the replay path.

Loads the trained model once and scores incoming 10-second windows. Two
guarantees matter here:

1. **Fail safe.** If the model is missing, stale, or raises, the scorer returns
   a `MONITORING_ONLY` forecast — never a low risk score. A broken lookout who
   reports "all clear" is worse than no lookout.
2. **Provenance.** Every forecast carries the model version, feature version
   and the exact feature values it was computed from, so the agents can quote
   evidence and the audit log can reconstruct the decision.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import (
    FEATURE_VERSION,
    HORIZON_SECONDS,
    PATHS,
    RISK_BAND_CRITICAL,
    RISK_BAND_WARN,
    RISK_BAND_WATCH,
    STATE_ABOUT_TO_START,
    STATE_MONITORING_ONLY,
)
from ..memory.features import FEATURE_COLUMNS

log = logging.getLogger("dfsi.forecaster.serve")

#: Features worth surfacing as human-readable evidence, with a short label.
EVIDENCE_LABELS: dict[str, str] = {
    "growth_pps_60s": "packet-rate growth over 60s",
    "growth_mbps_60s": "bandwidth growth over 60s",
    "growth_pps_10s": "packet-rate growth over 10s",
    "baseline_deviation_pps": "deviation from this hour's normal packet rate",
    "baseline_deviation_mbps": "deviation from this hour's normal bandwidth",
    "baseline_deviation_pps_max_hour": "strongest per-vector deviation on this range",
    "country_hhi": "source-country concentration",
    "country_hhi_delta_60s": "shift in source-country concentration over 60s",
    "account_attack_history": "prior attack episodes on this account",
    "blocked_share": "share of traffic currently blocked",
    "blocked_share_delta_60s": "change in blocked share over 60s",
    "anomaly_persistence": "consecutive abnormal windows",
    "policy_utilisation": "packet rate as a fraction of the configured policy",
    "policy_utilisation_mbps": "bandwidth as a fraction of the configured policy",
}


def risk_band(score: float) -> str:
    if score >= RISK_BAND_CRITICAL:
        return "critical"
    if score >= RISK_BAND_WARN:
        return "warn"
    if score >= RISK_BAND_WATCH:
        return "watch"
    return "calm"


@dataclass(frozen=True)
class Forecast:
    """One risk forecast for one range at one instant."""

    window_start_epoch: int
    range_id: int
    account_id: int
    risk_score: float | None
    risk_band: str
    predicted_state: str
    horizon_seconds: int
    model_version: str
    feature_version: str
    evidence: list[dict] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "window_start_epoch": self.window_start_epoch,
            "range_id": self.range_id,
            "account_id": self.account_id,
            "risk_score": self.risk_score,
            "risk_band": self.risk_band,
            "predicted_state": self.predicted_state,
            "horizon_seconds": self.horizon_seconds,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "evidence": self.evidence,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


class ForecasterService:
    """Loads the pickled forecaster and scores windows.

    Deliberately *not* thread-safe for mutation: the model is loaded once at
    construction and only read afterwards.
    """

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or (PATHS.models / "forecaster.pkl")
        self._model = None
        self._features: list[str] = list(FEATURE_COLUMNS)
        self._importance: dict[str, float] = {}
        self.model_version = "unavailable"
        self.feature_version = FEATURE_VERSION
        self.decision_threshold = 0.55
        self.min_consecutive = 2
        self.load_error: str | None = None
        self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.model_path.exists():
            self.load_error = (
                f"no trained model at {self.model_path}; "
                "run `python -m dfsi.forecaster.train`"
            )
            log.warning(self.load_error)
            return
        try:
            with self.model_path.open("rb") as handle:
                payload = pickle.load(handle)
            self._model = payload["model"]
            self._features = list(payload.get("features") or FEATURE_COLUMNS)
            self._importance = payload.get("feature_importance") or {}
            self.feature_version = payload.get("feature_version", FEATURE_VERSION)
            self.decision_threshold = float(payload.get("decision_threshold", 0.55))
            self.min_consecutive = int(payload.get("min_consecutive", 2))
            version = payload.get("model_version") or ""
            run_id = payload.get("mlflow_run_id") or ""
            self.model_version = (
                f"{self.feature_version}/v{version}" if version else f"{self.feature_version}/{run_id[:8] or 'local'}"
            )
            if self.feature_version != FEATURE_VERSION:
                self.load_error = (
                    f"model was trained on {self.feature_version} but this build "
                    f"computes {FEATURE_VERSION}; refusing to score"
                )
                self._model = None
                log.error(self.load_error)
        except Exception as exc:
            self.load_error = f"could not load forecaster: {exc}"
            self._model = None
            log.exception("forecaster load failed")

    @property
    def ready(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------ #
    def score_window(self, windows: list[dict]) -> Forecast:
        """Score one instant for one range.

        `windows` holds the per-traffic-type rows for a single
        (range_id, window_start_epoch). The range-level risk is the maximum
        across its traffic types: one vector is enough to ring the bell.
        """
        if not windows:
            raise ValueError("score_window requires at least one traffic-type row")

        head = windows[0]
        epoch = int(head["window_start_epoch"])
        range_id = int(head["range_id"])
        account_id = int(head.get("account_id", -1))

        if not self.ready:
            return self._degraded(epoch, range_id, account_id, self.load_error or "model not loaded")

        try:
            frame = pd.DataFrame(windows)
            missing = [c for c in self._features if c not in frame.columns]
            if missing:
                return self._degraded(
                    epoch, range_id, account_id, f"missing features {missing}"
                )
            scores = self._model.predict_proba(frame[self._features])[:, 1]
            best = int(scores.argmax())
            score = float(scores[best])
        except Exception as exc:
            log.exception("scoring failed for range %s", range_id)
            return self._degraded(epoch, range_id, account_id, f"scoring error: {exc}")

        band = risk_band(score)
        return Forecast(
            window_start_epoch=epoch,
            range_id=range_id,
            account_id=account_id,
            risk_score=score,
            risk_band=band,
            predicted_state=(
                STATE_ABOUT_TO_START if score >= self.decision_threshold else "QUIET"
            ),
            horizon_seconds=HORIZON_SECONDS,
            model_version=self.model_version,
            feature_version=self.feature_version,
            evidence=self.build_evidence(windows[best], score),
        )

    def _degraded(self, epoch: int, range_id: int, account_id: int, reason: str) -> Forecast:
        """Fail safe: monitoring-only, no risk number, never 'low risk'."""
        return Forecast(
            window_start_epoch=epoch,
            range_id=range_id,
            account_id=account_id,
            risk_score=None,
            risk_band="unknown",
            predicted_state=STATE_MONITORING_ONLY,
            horizon_seconds=HORIZON_SECONDS,
            model_version=self.model_version,
            feature_version=self.feature_version,
            evidence=[],
            degraded=True,
            degraded_reason=reason,
        )

    # ------------------------------------------------------------------ #
    def build_evidence(self, row: dict, score: float, top_n: int = 6) -> list[dict]:
        """Rank the features behind this score, highest importance first.

        These are the *only* numbers the agents may cite. The agents receive
        this list through a read-only tool and must quote it verbatim.
        """
        items: list[dict] = []
        for name, label in EVIDENCE_LABELS.items():
            if name not in row:
                continue
            value = row[name]
            if value is None:
                continue
            items.append(
                {
                    "feature": name,
                    "label": label,
                    "value": round(float(value), 4),
                    "importance": round(float(self._importance.get(name, 0.0)), 6),
                    "traffic_type": row.get("traffic_type"),
                }
            )
        items.sort(key=lambda item: (item["importance"], abs(item["value"])), reverse=True)
        return items[:top_n]
