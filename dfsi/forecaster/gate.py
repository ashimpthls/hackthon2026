"""Evidence gate — context alone does not raise an alert.

The forecaster leans on context as well as traffic: how many attack episodes
this account has had recently, for one. That is a legitimate prior (attacks come
in campaigns), but it means a perfectly ordinary flash crowd on an account that
was attacked last week can score above the threshold with the traffic itself
still inside its normal range for the hour. On the held-out test week, which
flash crowds alerted was predicted by account attack history (AUC 0.82) far
better than by anything about the traffic, and about half of their alerting
windows showed no traffic abnormality at all.

The gate states the missing requirement: to raise an alert the traffic must be
abnormal for the hour, using the same bar the pipeline already uses for an
abnormal window (`ANOMALY_DEVIATION_THRESHOLD`, a robust z-score of 3). It is
deliberately the simplest thing that works. A second-stage classifier trained to
tell attacks from flash crowds was tried first and removed one point of flash
false alarms, because the flash crowds that alert look attack-like on traffic.

What the gate never does:

* it does not touch the risk score; the model's number stays the model's number,
  and a gated window still shows it, with the reason;
* it never raises an alert, only withholds one;
* it never acts on missing data. A window with no deviation figure has *unknown*
  evidence, and unknown fails open to alerting, the same rule as everywhere else
  in DFSI: not knowing never counts as all-clear, and never as a reason to stay
  silent either.

The same function drives the published metrics and live scoring, so the numbers
quoted about the system describe the system. It is *not* part of threshold
calibration: the threshold stays a property of the model, calibrated on the raw
score, and the gate is a second condition applied on top of it. Calibrating on
the gated score was tried and is unstable (see `train.calibrate_threshold`).
"""

from __future__ import annotations

import math

import pandas as pd

#: The feature the gate reads: the strongest per-vector deviation on the range at
#: this instant, as a robust z-score against the same hour of week.
EVIDENCE_FEATURE = "baseline_deviation_pps_max_hour"


def has_traffic_evidence(deviation: float | None, min_deviation: float | None) -> bool:
    """Is the traffic abnormal enough to support an alert?

    `min_deviation=None` means no gate (always True). A missing or non-finite
    deviation is unknown evidence and fails open (True).
    """
    if min_deviation is None:
        return True
    if deviation is None or not math.isfinite(float(deviation)):
        return True
    return float(deviation) > min_deviation


def apply_gate(
    frame: pd.DataFrame,
    score_column: str = "risk_score",
    min_deviation: float | None = None,
) -> pd.Series:
    """The score the alert rule sees: the model's where there is evidence, 0 where not.

    Zero rather than "capped just under threshold" so the result is correct at
    every threshold in a calibration sweep. Rows with an unknown deviation keep
    their score.
    """
    scores = frame[score_column].astype(float)
    if min_deviation is None or EVIDENCE_FEATURE not in frame.columns:
        return scores
    # `<=` is False for NaN, so unknown evidence is never gated.
    no_evidence = frame[EVIDENCE_FEATURE].astype(float) <= min_deviation
    return scores.where(~no_evidence, 0.0)
