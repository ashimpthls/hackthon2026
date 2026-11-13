"""Memory, part 3 — the pipeline driver.

    synthetic episodes -> raw windows (parquet)
                       -> hour-of-week baselines (parquet)
                       -> feature table + 120s label (parquet)
                       -> per-scenario replay files (json, for the UI)

Run with:  python -m dfsi.memory.pipeline --days 7
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from ..config import PATHS, SCENARIO_SET_VERSION, thresholds_for
from .features import (
    RAW_SCHEMA,
    build_features,
    build_spark,
    compute_baselines,
    with_time_keys,
)
from .scenarios import DEFAULT_RANGES, iter_rows, plan_week

log = logging.getLogger("dfsi.memory")

#: Columns a replay row needs: enough to re-score the window live in the API
#: and to draw the timeline, nothing more.
REPLAY_COLUMNS = (
    "scenario_id",
    "scenario_type",
    "window_start_epoch",
    "range_id",
    "account_id",
    "range_type",
    "traffic_type",
    "total_pps",
    "total_mbps",
    "blocked_pps",
    "blocked_mbps",
    "policy_pps",
    "policy_mbps",
    "country_hhi",
    "account_attack_history",
    "growth_pps_10s",
    "growth_pps_60s",
    "growth_mbps_10s",
    "growth_mbps_60s",
    "baseline_deviation_pps",
    "baseline_deviation_mbps",
    "baseline_deviation_pps_max_hour",
    "country_hhi_delta_60s",
    "blocked_share",
    "blocked_share_delta_10s",
    "blocked_share_delta_60s",
    "anomaly_persistence",
    "policy_utilisation",
    "policy_utilisation_mbps",
    "range_over_threshold",
    "declared",
    "is_onset",
    "label_onset_120s",
)


def run(days: int = 7, environment: str | None = None, replay_per_type: int = 2) -> dict:
    """Build the whole Memory layer. Returns a summary dict."""
    thresholds = thresholds_for(environment)
    for directory in (PATHS.raw_windows, PATHS.baselines, PATHS.features, PATHS.replay):
        directory.parent.mkdir(parents=True, exist_ok=True)

    plans = plan_week(DEFAULT_RANGES, days=days)
    log.info("planned %d episodes over %d days", len(plans), days)

    spark = build_spark()
    try:
        rows = list(iter_rows(plans))
        log.info("generated %d raw windows", len(rows))
        raw = spark.createDataFrame(rows, schema=RAW_SCHEMA)
        raw.write.mode("overwrite").parquet(str(PATHS.raw_windows))
        raw = spark.read.parquet(str(PATHS.raw_windows))

        baselines = compute_baselines(with_time_keys(raw)).cache()
        baselines.write.mode("overwrite").parquet(str(PATHS.baselines))

        features = build_features(raw, baselines, thresholds).cache()
        features.write.mode("overwrite").parquet(str(PATHS.features))

        label_counts = {
            int(r["label_onset_120s"]): int(r["n"])
            for r in features.groupBy("label_onset_120s").count().withColumnRenamed("count", "n").collect()
        }
        by_type = {
            r["scenario_type"]: int(r["n"])
            for r in features.groupBy("scenario_type")
            .agg({"scenario_id": "count"})
            .withColumnRenamed("count(scenario_id)", "n")
            .collect()
        }
        _write_replays(features, replay_per_type)
        summary = {
            "scenario_set_version": SCENARIO_SET_VERSION,
            "environment": thresholds.environment,
            "episodes": len(plans),
            "windows": len(rows),
            "label_counts": label_counts,
            "rows_by_scenario_type": by_type,
            "features_path": str(PATHS.features),
        }
    finally:
        spark.stop()

    (PATHS.data / "memory_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _write_replays(features, per_type: int) -> None:
    """Export a handful of complete scenarios as JSON for the UI replay.

    One replay = every window of one episode for one range, ordered in time,
    so the dashboard can stream it without Spark in the loop.
    """
    PATHS.replay.mkdir(parents=True, exist_ok=True)
    catalogue: list[dict] = []

    picks = (
        features.select("scenario_id", "scenario_type", "range_id", "label_onset_120s")
        .groupBy("scenario_id", "scenario_type", "range_id")
        .max("label_onset_120s")
        .withColumnRenamed("max(label_onset_120s)", "has_onset")
        .collect()
    )
    chosen: dict[str, list] = {}
    for row in sorted(picks, key=lambda r: (r["scenario_type"], r["scenario_id"])):
        bucket = chosen.setdefault(row["scenario_type"], [])
        if len(bucket) < per_type:
            bucket.append(row)

    for scenario_type, rows in chosen.items():
        for row in rows:
            subset = (
                features.filter(
                    (features.scenario_id == row["scenario_id"])
                    & (features.range_id == row["range_id"])
                )
                .select(*REPLAY_COLUMNS)
                .orderBy("window_start_epoch", "traffic_type")
                .collect()
            )
            replay_id = f"{scenario_type}__{row['scenario_id']}"
            payload = {
                "replay_id": replay_id,
                "scenario_type": scenario_type,
                "scenario_id": row["scenario_id"],
                "range_id": row["range_id"],
                "has_onset": bool(row["has_onset"]),
                "windows": [r.asDict() for r in subset],
            }
            path: Path = PATHS.replay / f"{replay_id}.json"
            path.write_text(json.dumps(payload))
            catalogue.append(
                {
                    "replay_id": replay_id,
                    "scenario_type": scenario_type,
                    "range_id": row["range_id"],
                    "has_onset": bool(row["has_onset"]),
                    "window_count": len({r["window_start_epoch"] for r in subset}),
                }
            )

    (PATHS.replay / "catalogue.json").write_text(
        json.dumps(sorted(catalogue, key=lambda c: c["replay_id"]), indent=2)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the DFSI Memory layer")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--env", default=None, help="stage (default) or prod thresholds")
    parser.add_argument("--replay-per-type", type=int, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary = run(days=args.days, environment=args.env, replay_per_type=args.replay_per_type)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
