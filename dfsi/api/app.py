"""DFSI API — the trigger surface and the live event stream.

Endpoints
---------
GET  /api/health              service, model and agent status
GET  /api/replays             the scenario catalogue (the trigger menu)
GET  /api/model-card          what the model is and how it scored
POST /api/replay/stream       SSE: stream a scenario past the forecaster
POST /api/drill               run the rehearsal room on a replay
POST /api/explain             run the two Explainer agents + validator
POST /api/decision            record a simulated human decision
GET  /api/decisions           read the audit log

Everything is read-only with respect to the DDoS control plane. The only write
this service performs is appending to its own decision audit log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..agents.orchestrator import ExplainerOrchestrator
from ..agents.tools import EvidenceBundle
from ..config import (
    FEATURE_VERSION,
    HORIZON_SECONDS,
    PATHS,
    STATE_ABOUT_TO_START,
    thresholds_for,
)
from ..forecaster.alerting import AlertTracker
from ..forecaster.range_thresholds import load_range_thresholds
from ..forecaster.serve import ForecasterService
from ..rehearsal.policy_advisor import PolicyPlan, suggest_policy
from ..rehearsal.policy_store import PolicyStore, apply_policy
from ..rehearsal.simulator import CANDIDATES_BY_KEY, run_drill
from ..rehearsal.uncertainty import assumption_uncertainty
from ..rehearsal.waiting import cost_of_waiting
from . import replay as replay_engine
from .schemas import (
    DecisionRecord,
    DecisionRequest,
    DrillRequest,
    ExplainRequest,
    HealthResponse,
    ModelCardResponse,
    ReplayRequest,
)

log = logging.getLogger("dfsi.api")

KNOWN_LIMITATIONS = [
    "Trained and evaluated on synthetic traffic only. Real-world performance "
    "is unmeasured.",
    "Evaluated on one held-out synthetic week that follows every week the "
    "model was fitted or calibrated on. One week is a small sample; treat "
    "the headline figures as estimates, not guarantees.",
    "Recovery scenarios open mid-attack, so there is no runway to forecast "
    "from. They are excluded from the head-start figures and reported "
    "separately as `in_progress_detected` — a detection rate, not a warning.",
    "The head start is measured against the moment Shared State would "
    "declare, not the moment traffic crosses the policy threshold. On a "
    "production start duration of 300s the gap between those two is larger "
    "than it is on this scenario set.",
    "A flash crowd large enough to cross the policy threshold is a true onset "
    "by DFSI's label definition, even though no attacker is involved.",
    "The rehearsal room's suppression and collateral figures are stated "
    "assumptions, not measurements of Behemoth's actual behaviour.",
    "Source information is country-mix concentration only; an attack that is "
    "evenly distributed across many countries loses that signal.",
    "Approved threshold changes apply to DFSI's simulated, in-memory policy "
    "store (seeded from raw.json), not to Behemoth or any customer "
    "configuration. Restarting the API, or resetting the store, restores the seed.",
]

app = FastAPI(
    title="DFSI — DDoS Future-State Intelligence",
    description=(
        "Predictive lookout beside netsec-ddos-shared-state. Read-only and "
        "simulation-only: ML makes the numbers, AI explains them, humans decide."
    ),
    version="0.1.0",
)

# The dashboard is served by Vite in development, so its origin differs from
# the API's. Restricted to the local dev origins rather than "*" — a wildcard
# with credentials enabled is how a dashboard becomes an open proxy.
_DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("DFSI_CORS_ORIGINS", _DEFAULT_ORIGINS).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_forecaster = ForecasterService()
_orchestrator = ExplainerOrchestrator()
_policy_store = PolicyStore.from_file(PATHS.policy_db)
#: Per-range `risk_threshold` from raw.json; ranges not listed follow the model.
_range_thresholds = load_range_thresholds(PATHS.policy_db)


def _threshold_for(range_id: int) -> float:
    """The decision threshold a range is read against."""
    return _range_thresholds.get(int(range_id), _forecaster.decision_threshold)


def _load_replay(replay_id: str) -> dict:
    """A stored replay, re-derived under the policy currently in the store.

    Every endpoint goes through this, so the stream, the drill, the agents and
    the decision all see the same policy. Raises KeyError for unknown ids.
    """
    replay = replay_engine.load_replay(replay_id)
    range_id = int(replay["range_id"])
    policy = _policy_store.range_policy(range_id)
    replay = dict(replay)
    replay["windows"] = apply_policy(replay["windows"], policy)
    replay["policy_version"] = _policy_store.range_version(range_id)
    replay["policy"] = (
        {t: {"max_threshold_pps": p.pps, "max_threshold_mbps": round(p.mbps, 3)} for t, p in sorted(policy.items())}
        if policy
        else None
    )
    return replay


# --------------------------------------------------------------------------- #
# Policy store (simulated)
# --------------------------------------------------------------------------- #
@app.get("/api/policies")
def list_policies() -> dict:
    """The simulated policy store: current lines per range and type, and history."""
    return _policy_store.snapshot()


@app.post("/api/policies/reset")
def reset_policies() -> dict:
    """Restore the store to its seed (raw.json)."""
    _policy_store.reset()
    return _policy_store.snapshot()


# --------------------------------------------------------------------------- #
# Per-range decision thresholds
# --------------------------------------------------------------------------- #
@app.get("/api/risk-thresholds")
def list_risk_thresholds() -> dict:
    """The model's decision threshold, and each range's `risk_threshold` from raw.json."""
    return {
        "model_default": _forecaster.decision_threshold,
        "ranges": {str(k): v for k, v in sorted(_range_thresholds.items())},
    }


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    catalogue = replay_engine.load_catalogue()
    return HealthResponse(
        status="ok" if _forecaster.ready and catalogue else "degraded",
        forecaster_ready=_forecaster.ready,
        forecaster_error=_forecaster.load_error,
        model_version=_forecaster.model_version,
        feature_version=_forecaster.feature_version,
        horizon_seconds=HORIZON_SECONDS,
        agent_engine=_orchestrator.engine,
        replays_available=len(catalogue),
        environment=thresholds_for().environment,
    )


@app.get("/api/replays")
def list_replays() -> dict:
    catalogue = replay_engine.load_catalogue()
    if not catalogue:
        raise HTTPException(
            status_code=503,
            detail=(
                "no replays available; run `python -m dfsi.memory.pipeline` to "
                "build the Memory layer"
            ),
        )
    return {
        "replays": [entry.to_dict() for entry in catalogue],
        "scenario_types": sorted({entry.scenario_type for entry in catalogue}),
    }


@app.get("/api/model-card", response_model=ModelCardResponse)
def model_card() -> ModelCardResponse:
    path: Path = PATHS.models / "training_report.json"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="no training report; run `python -m dfsi.forecaster.train`",
        )
    report = json.loads(path.read_text())
    return ModelCardResponse(
        model_version=_forecaster.model_version,
        feature_version=report.get("feature_version", FEATURE_VERSION),
        horizon_seconds=report.get("horizon_seconds", HORIZON_SECONDS),
        decision_threshold=report.get("decision_threshold", _forecaster.decision_threshold),
        features=report.get("features", []),
        headline_metrics=report.get("dfsi", {}),
        baseline_metrics=report.get("fixed_threshold_baseline", {}),
        per_scenario_type=report.get("per_scenario_type", {}),
        feature_importance=report.get("feature_importance", {}),
        known_limitations=KNOWN_LIMITATIONS,
    )


# --------------------------------------------------------------------------- #
# The trigger: stream a scenario replay
# --------------------------------------------------------------------------- #
@app.post("/api/replay/stream")
async def stream_replay(request: ReplayRequest) -> StreamingResponse:
    """Server-sent events: one `window` event per 10-second window.

    The stream is the demo. It ends with a `summary` event carrying the lead
    time DFSI achieved against the threshold alarm.
    """
    try:
        replay = _load_replay(request.replay_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown replay {request.replay_id!r}")
    state = replay_engine.build_state(replay)
    policy_version = replay["policy_version"]
    policy = replay["policy"]

    async def events():
        yield _sse(
            {
                "type": "start",
                "replay_id": state.replay_id,
                "scenario_type": state.scenario_type,
                "range_id": state.range_id,
                "total_windows": state.total,
                "horizon_seconds": HORIZON_SECONDS,
                "model_version": _forecaster.model_version,
                "feature_version": _forecaster.feature_version,
                "forecaster_ready": _forecaster.ready,
                "policy_version": policy_version,
                "policy": policy,
                "decision_threshold": _threshold_for(state.range_id),
            }
        )
        windows_after_bell = 0
        try:
            while not state.finished:
                event = await asyncio.to_thread(
                    replay_engine.step, state, _forecaster, _threshold_for(state.range_id)
                )
                yield _sse(event)

                if state.bell_epoch is not None:
                    windows_after_bell += 1
                    if (
                        request.stop_after_bell_windows
                        and windows_after_bell >= request.stop_after_bell_windows
                    ):
                        break
                if request.speed_seconds:
                    await asyncio.sleep(request.speed_seconds)
        except asyncio.CancelledError:
            # Client navigated away. Nothing to clean up — the replay holds no
            # external resources — but do not swallow the cancellation.
            raise
        except Exception as exc:
            log.exception("replay %s failed", state.replay_id)
            yield _sse({"type": "error", "message": f"replay failed: {exc}"})
            return

        yield _sse(
            {
                "type": "summary",
                "replay_id": state.replay_id,
                "scenario_type": state.scenario_type,
                "first_warning_epoch": state.first_warning_epoch,
                "onset_epoch": state.onset_epoch,
                "bell_epoch": state.bell_epoch,
                "lead_seconds": (
                    state.onset_epoch - state.first_warning_epoch
                    if state.onset_epoch is not None and state.first_warning_epoch is not None
                    else None
                ),
                "windows_streamed": state.cursor,
                "policy_version": policy_version,
                "policy": policy,
                **replay_engine.run_stats(state),
                # One entry per incident, not per flicker: when each alert
                # opened and closed, how high it peaked, whether it escalated.
                "alerts": replay_engine.alert_summary(state),
            }
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# --------------------------------------------------------------------------- #
# Rehearsal room
# --------------------------------------------------------------------------- #
@app.post("/api/drill")
def drill(request: DrillRequest) -> dict:
    replay, decision_epoch = _replay_and_decision_epoch(
        request.replay_id, request.decision_epoch
    )
    plan = _policy_plan(replay, decision_epoch, request.environment)
    result = run_drill(replay["windows"], decision_epoch, request.environment, plan)
    result["replay_id"] = request.replay_id
    result["scenario_type"] = replay["scenario_type"]
    if request.include_uncertainty:
        result["uncertainty"] = assumption_uncertainty(
            replay["windows"],
            decision_epoch,
            request.environment,
            plan,
            draws=request.uncertainty_draws,
        )
    if request.include_waiting:
        try:
            result["cost_of_waiting"] = cost_of_waiting(
                replay["windows"],
                decision_epoch,
                request.waiting_seconds,
                request.environment,
                plan,
            )
        except ValueError as exc:  # a wait that is not whole windows is the caller's mistake
            raise HTTPException(status_code=422, detail=str(exc))
    return result


# --------------------------------------------------------------------------- #
# Explainers
# --------------------------------------------------------------------------- #
@app.post("/api/explain")
def explain(request: ExplainRequest) -> dict:
    replay, decision_epoch = _replay_and_decision_epoch(
        request.replay_id, request.decision_epoch
    )
    bundle = _build_bundle(replay, decision_epoch, request.include_simulation)
    briefing = _orchestrator.explain(bundle)
    briefing["replay_id"] = request.replay_id
    briefing["scenario_type"] = replay["scenario_type"]
    briefing["decision_epoch"] = decision_epoch
    return briefing


def _build_bundle(
    replay: dict, decision_epoch: int, include_simulation: bool
) -> EvidenceBundle:
    """Assemble exactly what the agents are allowed to see.

    Only windows at or before `decision_epoch` are included: an agent that can
    see the future would explain the incident beautifully and uselessly.
    """
    rows = [
        row for row in replay["windows"] if int(row["window_start_epoch"]) <= decision_epoch
    ]
    if not rows:
        raise HTTPException(
            status_code=400, detail=f"no windows at or before epoch {decision_epoch}"
        )

    current = [row for row in rows if int(row["window_start_epoch"]) == decision_epoch]
    if not current:
        raise HTTPException(
            status_code=400, detail=f"epoch {decision_epoch} is not a window boundary"
        )

    forecast = _forecaster.score_window(current, _threshold_for(int(replay["range_id"])))
    lookback_epochs = sorted({int(r["window_start_epoch"]) for r in rows})[
        -replay_engine.EVIDENCE_LOOKBACK :
    ]
    recent = [row for row in rows if int(row["window_start_epoch"]) in set(lookback_epochs)]

    simulation = (
        run_drill(replay["windows"], decision_epoch, None, _policy_plan(replay, decision_epoch))
        if include_simulation
        else None
    )
    return EvidenceBundle(
        range_id=forecast.range_id,
        account_id=forecast.account_id,
        window_start_epoch=decision_epoch,
        risk_score=forecast.risk_score,
        risk_band=forecast.risk_band,
        model_version=forecast.model_version,
        feature_version=forecast.feature_version,
        horizon_seconds=forecast.horizon_seconds,
        evidence=forecast.evidence,
        recent_windows=recent,
        simulation=simulation,
        tenant_account_id=forecast.account_id,
        # A replay's "now" is the instant the operator is deciding at; any
        # window after it would be evidence from the future.
        as_of_epoch=decision_epoch,
    )


# --------------------------------------------------------------------------- #
# Human decision (simulated, audited)
# --------------------------------------------------------------------------- #
@app.post("/api/decision", response_model=DecisionRecord)
def record_decision(request: DecisionRequest) -> DecisionRecord:
    """Record a human decision, and apply an approved threshold change.

    Approving "tighten the vector threshold" writes the suggested per-type
    lines into DFSI's simulated policy store, so the next run of any replay on
    that range is played under them. Every other decision is recorded only.
    Nothing reaches Behemoth, Shared State or a customer's configuration.
    Every record is traceable to the model version and the policy version.
    """
    try:
        replay = _load_replay(request.replay_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown replay {request.replay_id!r}")

    candidate = CANDIDATES_BY_KEY[request.policy_key]
    policy_change = None
    applied = False
    policy_version_before = replay["policy_version"]
    policy_version_after = policy_version_before
    if candidate.key == "tighten_vector":
        # Recomputed here rather than trusted from the client, so the audit
        # record states exactly which thresholds the human signed off on.
        plan = _policy_plan(replay, request.decision_epoch)
        policy_change = {
            "basis": "past-week thresholds" if plan.available else "stated assumption",
            "change": plan.describe_change() if plan.available else None,
            "reason": plan.reason,
            "per_type": [
                {
                    "traffic_type": s.traffic_type,
                    "current_policy_pps": s.current_policy_pps,
                    "suggested_policy_pps": s.suggested_policy_pps,
                    "backtest_legit_lost_pct": s.backtest_legit_lost_pct,
                }
                for s in plan.per_type
                if s.apply
            ],
        }
        new_lines = plan.applied_thresholds if plan.available else {}
        if request.verdict in ("approved", "changed") and new_lines:
            policy_version_after = _policy_store.apply_change(
                int(replay["range_id"]),
                new_lines,
                provenance={
                    "replay_id": request.replay_id,
                    "decision_epoch": request.decision_epoch,
                    "operator": request.operator,
                    "verdict": request.verdict,
                    "basis": "past-week thresholds",
                },
            )
            applied = True

    if applied:
        note_to_auditor = (
            f"Applied to DFSI's simulated policy store (version {policy_version_after}). "
            "Replays on this range now run under the new lines. No real policy, "
            "Behemoth rule or customer configuration was changed."
        )
    else:
        note_to_auditor = (
            "Decision recorded for audit. Only an approved threshold change is "
            "applied, and only to DFSI's simulated policy store; nothing changed."
        )
    record = DecisionRecord(
        recorded_at_epoch=int(time.time()),
        replay_id=request.replay_id,
        decision_epoch=request.decision_epoch,
        policy_key=candidate.key,
        policy_name=candidate.name,
        verdict=request.verdict,
        operator=request.operator,
        note=request.note,
        model_version=request.model_version,
        feature_version=_forecaster.feature_version,
        policy_change=policy_change,
        applied=applied,
        policy_version_before=policy_version_before,
        policy_version_after=policy_version_after,
        note_to_auditor=note_to_auditor,
    )

    PATHS.decisions.mkdir(parents=True, exist_ok=True)
    with (PATHS.decisions / "decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.model_dump()) + "\n")
    return record


@app.get("/api/decisions")
def list_decisions(limit: int = 50) -> dict:
    path = PATHS.decisions / "decisions.jsonl"
    if not path.exists():
        return {"decisions": []}
    bounded = max(1, min(limit, 500))
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return {"decisions": [json.loads(line) for line in lines[-bounded:]][::-1]}


# --------------------------------------------------------------------------- #
def _policy_plan(
    replay: dict, decision_epoch: int, environment: str | None = None
) -> PolicyPlan:
    """Per-type threshold suggestion from the week of traffic before this episode."""
    windows = replay["windows"]
    episode_start = min(int(row["window_start_epoch"]) for row in windows)
    current = [row for row in windows if int(row["window_start_epoch"]) == decision_epoch]
    try:
        return suggest_policy(int(replay["range_id"]), episode_start, current, environment)
    except Exception as exc:  # history is optional; the drill still runs without it
        log.exception("policy suggestion failed for %s", replay.get("replay_id"))
        return PolicyPlan(
            available=False,
            range_id=int(replay["range_id"]),
            reason=f"history could not be read: {exc}",
        )


def _replay_and_decision_epoch(
    replay_id: str, decision_epoch: int | None
) -> tuple[dict, int]:
    """Load a replay and resolve the instant the operator is deciding at.

    When unspecified, this is the first window DFSI warned about — the moment
    the operator would actually have been looking at the dashboard. Falling
    back to the last window keeps the endpoint usable on quiet scenarios.
    """
    try:
        replay = _load_replay(replay_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown replay {replay_id!r}")

    epochs = sorted({int(row["window_start_epoch"]) for row in replay["windows"]})
    if decision_epoch is not None:
        if decision_epoch not in epochs:
            raise HTTPException(
                status_code=400,
                detail=f"epoch {decision_epoch} is not a window in this replay",
            )
        return replay, decision_epoch

    buckets: dict[int, list[dict]] = {}
    for row in replay["windows"]:
        buckets.setdefault(int(row["window_start_epoch"]), []).append(row)
    # The same debounce the stream applies, so "the first window DFSI warned
    # about" is the window the dashboard actually raised the warning at.
    tracker = AlertTracker(_forecaster.min_consecutive)
    threshold = _threshold_for(int(replay["range_id"]))
    for epoch in epochs:
        forecast = _forecaster.score_window(buckets[epoch], threshold)
        if tracker.update(forecast.predicted_state == STATE_ABOUT_TO_START):
            return replay, epoch
    return replay, epochs[-1]
