"""Forecaster — the lookout with a spyglass.

Trains gradient-boosted trees to predict whether qualifying blocking will
*begin* within the next 120 seconds, and records everything in MLflow: params,
the three headline metrics, the fixed-threshold baseline comparison, the
feature list, and a registered model version.

Leakage discipline (the part that makes the numbers trustworthy):

* the split is by whole scenario — `GroupShuffleSplit` on `scenario_id`, so no
  window from a test scenario is ever in training, and neighbouring windows
  can never straddle the split;
* `scenario_id` and `scenario_type` are dropped before fitting;
* the feature list is `FEATURE_COLUMNS`, which contains no future-derived
  column (the label and the `declared`/`is_onset` bookkeeping stay out);
* stratification is approximated by grouping attack and quiet scenarios and
  splitting each pool, so rare onsets appear on both sides.

Run with:  python -m dfsi.forecaster.train
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit

from ..config import (
    FEATURE_VERSION,
    HORIZON_SECONDS,
    MLFLOW_EXPERIMENT,
    MLFLOW_TRACKING_URI,
    PATHS,
    REGISTERED_MODEL_NAME,
    thresholds_for,
)
from ..memory.features import FEATURE_COLUMNS, GROUP_COLUMN, LABEL_COLUMN
from .metrics import evaluate, fixed_threshold_baseline, lead_time_histogram, scenario_outcomes

log = logging.getLogger("dfsi.forecaster")

#: Columns that exist in the feature table but must never reach the model.
FORBIDDEN_INPUTS = frozenset(
    {
        LABEL_COLUMN,
        GROUP_COLUMN,
        "scenario_type",
        "is_onset",
        "declared",
        "sustained_windows",
        "range_over_threshold",
        "over_threshold",
        "is_abnormal",
        "blocked_pps",
        "blocked_mbps",
    }
)


@dataclass(frozen=True)
class TrainConfig:
    max_iter: int = 300
    learning_rate: float = 0.06
    max_leaf_nodes: int = 31
    min_samples_leaf: int = 40
    l2_regularization: float = 1.0
    early_stopping: bool = True
    validation_fraction: float = 0.15
    test_size: float = 0.3
    random_state: int = 7
    #: Fraction of the *training* scenarios held back to calibrate the decision
    #: threshold. Calibrating on the test split would leak.
    calibration_size: float = 0.25
    #: Highest share of quiet scenarios allowed to raise a warning. The
    #: threshold is chosen as the lowest value that respects this budget, which
    #: maximises lead time for a false-alarm rate an operator will tolerate.
    false_alarm_budget: float = 0.03
    #: Used only if calibration is skipped or finds no admissible threshold.
    decision_threshold: float = 0.55
    #: Consecutive windows above threshold required to raise a warning.
    min_consecutive: int = 2
    calibrate: bool = True


def assert_no_leakage(columns: list[str]) -> None:
    """Fail loudly if a forbidden column ever creeps into the feature list."""
    offenders = sorted(set(columns) & FORBIDDEN_INPUTS)
    if offenders:
        raise ValueError(
            "feature list contains future-derived or identifying columns: "
            f"{offenders}. These must not be model inputs."
        )


def load_features() -> pd.DataFrame:
    if not PATHS.features.exists():
        raise FileNotFoundError(
            f"no feature table at {PATHS.features}; run `python -m dfsi.memory.pipeline` first"
        )
    frame = pd.read_parquet(PATHS.features)
    missing = [c for c in FEATURE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"feature table is missing columns {missing}")
    return frame


def split_by_scenario(
    frame: pd.DataFrame, test_size: float, random_state: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Group split on `scenario_id`, balanced across attack / quiet pools."""
    per_scenario = (
        frame.groupby(GROUP_COLUMN)[LABEL_COLUMN].max().rename("has_onset").reset_index()
    )
    train_ids: list[str] = []
    test_ids: list[str] = []

    for has_onset, pool in per_scenario.groupby("has_onset"):
        ids = pool[GROUP_COLUMN].to_numpy()
        if ids.size < 2:
            train_ids.extend(ids.tolist())
            continue
        splitter = GroupShuffleSplit(
            n_splits=1, test_size=test_size, random_state=random_state + int(has_onset)
        )
        train_idx, test_idx = next(splitter.split(ids, groups=ids))
        train_ids.extend(ids[train_idx].tolist())
        test_ids.extend(ids[test_idx].tolist())

    overlap = set(train_ids) & set(test_ids)
    if overlap:
        raise AssertionError(f"scenario leaked across the split: {sorted(overlap)[:5]}")

    train = frame[frame[GROUP_COLUMN].isin(train_ids)].copy()
    test = frame[frame[GROUP_COLUMN].isin(test_ids)].copy()
    return train, test


def calibrate_threshold(
    model: HistGradientBoostingClassifier,
    calibration_frame: pd.DataFrame,
    config: TrainConfig,
) -> tuple[float, list[dict]]:
    """Choose the decision threshold on held-back *training* scenarios.

    A single hand-picked threshold is the wrong tool for a 0.3%-positive
    problem: set it high and the lookout stays silent, set it low and the
    operator stops listening. Instead, sweep thresholds and take the lowest one
    whose scenario-level false-alarm rate stays inside the budget — lower
    thresholds fire earlier, so this maximises lead time at an acceptable
    nuisance level.

    Returns the chosen threshold and the full sweep, which is logged so the
    choice is auditable rather than magic.
    """
    frame = calibration_frame.copy()
    frame["risk_score"] = model.predict_proba(frame[list(FEATURE_COLUMNS)])[:, 1]

    sweep: list[dict] = []
    for threshold in np.round(np.arange(0.05, 0.96, 0.05), 2):
        result = evaluate(frame, "risk_score", float(threshold), config.min_consecutive)
        sweep.append(
            {
                "threshold": float(threshold),
                "attacks_caught": result.attacks_caught,
                "false_alarm_rate": result.false_alarm_rate,
                "median_lead_seconds": result.median_lead_seconds,
            }
        )

    admissible = [row for row in sweep if row["false_alarm_rate"] <= config.false_alarm_budget]
    if not admissible:
        log.warning(
            "no threshold meets the %.1f%% false-alarm budget; keeping %.2f",
            config.false_alarm_budget * 100,
            config.decision_threshold,
        )
        return config.decision_threshold, sweep

    # Lowest admissible threshold = earliest warning within budget. Ties broken
    # towards the one that catches more attacks.
    chosen = min(admissible, key=lambda row: (row["threshold"], -row["attacks_caught"]))
    log.info(
        "calibrated threshold %.2f (false alarms %.1f%%, caught %.1f%%, median lead %.0fs)",
        chosen["threshold"],
        chosen["false_alarm_rate"] * 100,
        chosen["attacks_caught"] * 100,
        chosen["median_lead_seconds"],
    )
    return float(chosen["threshold"]), sweep


def permutation_importance(
    model: HistGradientBoostingClassifier,
    frame: pd.DataFrame,
    n_repeats: int = 3,
    random_state: int = 7,
) -> dict[str, float]:
    """Drop in average-precision when each feature is shuffled.

    Used for the evidence panel: the agents are allowed to *read* these and
    talk about them, but they never recompute or invent a number.
    """
    rng = np.random.default_rng(random_state)
    features = frame[list(FEATURE_COLUMNS)]
    labels = frame[LABEL_COLUMN].to_numpy()
    if labels.min() == labels.max():
        return {name: 0.0 for name in FEATURE_COLUMNS}

    base = average_precision_score(labels, model.predict_proba(features)[:, 1])
    importances: dict[str, float] = {}
    for name in FEATURE_COLUMNS:
        drops = []
        for _ in range(n_repeats):
            shuffled = features.copy()
            shuffled[name] = rng.permutation(shuffled[name].to_numpy())
            score = average_precision_score(labels, model.predict_proba(shuffled)[:, 1])
            drops.append(base - score)
        importances[name] = float(np.mean(drops))
    return importances


def quiet_reference(features: pd.DataFrame, labels: np.ndarray) -> dict[str, float]:
    """The typical value of each feature when nothing was about to happen.

    Median over the windows labelled 0. This is the yardstick for per-alert
    attribution (`ForecasterService.build_evidence`): a feature's contribution
    to a score is how much that score falls if the feature is put back to its
    quiet value. It is stored with the model so the same yardstick is used at
    serving time that the model was trained against.
    """
    quiet = features[np.asarray(labels) == 0]
    if quiet.empty:
        raise ValueError("no quiet windows in the training split to anchor attribution on")
    return {name: float(quiet[name].median()) for name in FEATURE_COLUMNS}


def train(config: TrainConfig | None = None, environment: str | None = None) -> dict:
    config = config or TrainConfig()
    thresholds = thresholds_for(environment)
    assert_no_leakage(list(FEATURE_COLUMNS))

    frame = load_features()
    dev_frame, test_frame = split_by_scenario(frame, config.test_size, config.random_state)
    # Carve the calibration set out of the development half only, so the test
    # scenarios stay untouched by every decision the pipeline makes.
    fit_frame, calib_frame = split_by_scenario(
        dev_frame, config.calibration_size, config.random_state + 100
    )
    log.info(
        "split: %d fit / %d calibration / %d test windows across %d / %d / %d scenarios",
        len(fit_frame),
        len(calib_frame),
        len(test_frame),
        fit_frame[GROUP_COLUMN].nunique(),
        calib_frame[GROUP_COLUMN].nunique(),
        test_frame[GROUP_COLUMN].nunique(),
    )

    x_fit = fit_frame[list(FEATURE_COLUMNS)]
    y_fit = fit_frame[LABEL_COLUMN].to_numpy()
    x_test = test_frame[list(FEATURE_COLUMNS)]
    y_test = test_frame[LABEL_COLUMN].to_numpy()

    model = HistGradientBoostingClassifier(
        max_iter=config.max_iter,
        learning_rate=config.learning_rate,
        max_leaf_nodes=config.max_leaf_nodes,
        min_samples_leaf=config.min_samples_leaf,
        l2_regularization=config.l2_regularization,
        early_stopping=config.early_stopping,
        validation_fraction=config.validation_fraction,
        # Onsets are ~0.3% of windows. Without reweighting, the model is
        # rewarded for predicting "quiet" forever and the scores never reach a
        # usable threshold.
        class_weight="balanced",
        random_state=config.random_state,
    )
    model.fit(x_fit, y_fit)

    threshold = config.decision_threshold
    sweep: list[dict] = []
    if config.calibrate and len(calib_frame):
        threshold, sweep = calibrate_threshold(model, calib_frame, config)

    test_frame["risk_score"] = model.predict_proba(x_test)[:, 1]
    test_frame["baseline_score"] = fixed_threshold_baseline(test_frame)

    dfsi_eval = evaluate(test_frame, "risk_score", threshold, config.min_consecutive)
    # The bell needs no debounce — persistence is already baked into the start
    # duration it waited out — so the incumbent is scored on a single window.
    base_eval = evaluate(test_frame, "baseline_score", 1.0, min_consecutive=1)
    outcomes = scenario_outcomes(test_frame, "risk_score", threshold, config.min_consecutive)

    window_metrics = {
        "roc_auc": float(roc_auc_score(y_test, test_frame["risk_score"]))
        if y_test.min() != y_test.max()
        else float("nan"),
        "average_precision": float(average_precision_score(y_test, test_frame["risk_score"]))
        if y_test.min() != y_test.max()
        else float("nan"),
        "positive_rate_fit": float(y_fit.mean()),
        "positive_rate_test": float(y_test.mean()),
        "decision_threshold": float(threshold),
    }
    importances = permutation_importance(model, test_frame, random_state=config.random_state)

    report = {
        "feature_version": FEATURE_VERSION,
        "environment": thresholds.environment,
        "horizon_seconds": HORIZON_SECONDS,
        "features": list(FEATURE_COLUMNS),
        "config": asdict(config),
        "decision_threshold": float(threshold),
        "threshold_sweep": sweep,
        "window_metrics": window_metrics,
        "dfsi": asdict(dfsi_eval),
        "fixed_threshold_baseline": asdict(base_eval),
        "lead_time_histogram": lead_time_histogram(outcomes),
        "feature_importance": dict(
            sorted(importances.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "feature_reference": quiet_reference(x_fit, y_fit),
        "per_scenario_type": _per_type_breakdown(test_frame, threshold, config),
    }

    run_id, model_version = _log_to_mlflow(model, config, report, x_fit.head(5))
    report["mlflow_run_id"] = run_id
    report["model_version"] = model_version

    PATHS.models.mkdir(parents=True, exist_ok=True)
    (PATHS.models / "training_report.json").write_text(json.dumps(report, indent=2, default=str))
    _save_local_model(model, report)
    return report


def _per_type_breakdown(
    test_frame: pd.DataFrame, threshold: float, config: TrainConfig
) -> dict:
    out: dict[str, dict] = {}
    for scenario_type, group in test_frame.groupby("scenario_type"):
        result = evaluate(group, "risk_score", threshold, config.min_consecutive)
        out[str(scenario_type)] = asdict(result)
    return out


def _save_local_model(model, report: dict) -> None:
    """Persist the model next to its report so the API can run without MLflow."""
    import pickle

    PATHS.models.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "features": list(FEATURE_COLUMNS),
        "feature_version": FEATURE_VERSION,
        "decision_threshold": report["decision_threshold"],
        "min_consecutive": report["config"]["min_consecutive"],
        "model_version": report.get("model_version"),
        "mlflow_run_id": report.get("mlflow_run_id"),
        "feature_importance": report["feature_importance"],
        "feature_reference": report["feature_reference"],
    }
    with (PATHS.models / "forecaster.pkl").open("wb") as handle:
        pickle.dump(payload, handle)


def _log_to_mlflow(model, config: TrainConfig, report: dict, input_example) -> tuple[str, str]:
    """Track the run and register a model version. Degrades to a no-op."""
    try:
        import mlflow
        import mlflow.sklearn
    except ImportError:  # pragma: no cover - mlflow is a declared dependency
        log.warning("mlflow unavailable; skipping tracking")
        return "", ""

    PATHS.mlruns.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)

    with mlflow.start_run() as run:
        mlflow.log_params(
            {
                **asdict(config),
                "feature_version": FEATURE_VERSION,
                "horizon_seconds": HORIZON_SECONDS,
                "n_features": len(FEATURE_COLUMNS),
                "model_family": "HistGradientBoostingClassifier",
                "split": "GroupShuffleSplit(scenario_id)",
            }
        )
        mlflow.log_metrics(report["window_metrics"] | {})
        mlflow.log_metrics(
            {f"dfsi_{k}": v for k, v in report["dfsi"].items() if isinstance(v, (int, float))}
        )
        mlflow.log_metrics(
            {
                f"baseline_{k}": v
                for k, v in report["fixed_threshold_baseline"].items()
                if isinstance(v, (int, float))
            }
        )
        mlflow.log_dict(report, "training_report.json")
        mlflow.log_dict({"features": list(FEATURE_COLUMNS)}, "feature_list.json")

        version = ""
        try:
            # mlflow 2.x takes `artifact_path`; 3.x renamed it to `name`.
            info = mlflow.sklearn.log_model(
                model,
                artifact_path="model",
                input_example=input_example,
                registered_model_name=REGISTERED_MODEL_NAME,
            )
            version = getattr(info, "registered_model_version", None) or ""
            if not version:
                client = mlflow.MlflowClient()
                versions = client.search_model_versions(f"name='{REGISTERED_MODEL_NAME}'")
                mine = [v for v in versions if v.run_id == run.info.run_id]
                if mine:
                    version = mine[0].version
        except Exception as exc:  # registry is optional on a file-based store
            log.warning("model registration skipped: %s", exc)
        return run.info.run_id, str(version)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the DFSI onset forecaster")
    parser.add_argument("--max-iter", type=int, default=TrainConfig.max_iter)
    parser.add_argument("--threshold", type=float, default=TrainConfig.decision_threshold)
    parser.add_argument(
        "--false-alarm-budget", type=float, default=TrainConfig.false_alarm_budget
    )
    parser.add_argument(
        "--no-calibrate",
        action="store_true",
        help="use --threshold verbatim instead of calibrating it",
    )
    parser.add_argument("--env", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    report = train(
        TrainConfig(
            max_iter=args.max_iter,
            decision_threshold=args.threshold,
            false_alarm_budget=args.false_alarm_budget,
            calibrate=not args.no_calibrate,
        ),
        environment=args.env,
    )
    print(
        json.dumps(
            {
                "decision_threshold": report["decision_threshold"],
                "dfsi": report["dfsi"],
                "baseline": report["fixed_threshold_baseline"],
                "window_metrics": report["window_metrics"],
                "per_scenario_type": {
                    k: {
                        "attacks_caught": v["attacks_caught"],
                        "false_alarm_rate": v["false_alarm_rate"],
                        "median_lead_seconds": v["median_lead_seconds"],
                    }
                    for k, v in report["per_scenario_type"].items()
                },
                "mlflow_run_id": report["mlflow_run_id"],
                "model_version": report["model_version"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
