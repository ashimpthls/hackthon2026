"""Per-range decision thresholds, from raw.json.

A range may set its own `risk_threshold` in raw.json instead of using the
model's. Only how a score is read changes: its band, whether it counts towards
the warning, and so the head start.
"""

from __future__ import annotations

import importlib
import json

import pytest

from conftest import EPOCH, RANGE_ID
from dfsi.api import replay as replay_engine
from dfsi.config import PATHS
from dfsi.forecaster.range_thresholds import load_range_thresholds
from dfsi.forecaster.serve import ForecasterService
from test_alerting import ScriptedForecaster, make_replay

SCORES = [0.1, 0.6, 0.6, 0.6, 0.6, 0.6]  # above 0.5, below 0.7


def write_db(tmp_path, ranges: list[dict]):
    path = tmp_path / "raw.json"
    path.write_text(json.dumps({"accounts": [{"account_id": 1, "ranges": ranges}]}))
    return path


# ------------------------------------------------------------------ loading


def test_only_ranges_that_set_a_threshold_are_overridden(tmp_path):
    path = write_db(
        tmp_path,
        [
            {"range_id": RANGE_ID, "risk_threshold": 0.7, "traffic_types": {}},
            {"range_id": RANGE_ID + 1, "traffic_types": {}},
            {"range_id": RANGE_ID + 2, "risk_threshold": None, "traffic_types": {}},
        ],
    )
    assert load_range_thresholds(path) == {RANGE_ID: 0.7}


def test_a_missing_file_means_no_overrides(tmp_path):
    assert load_range_thresholds(tmp_path / "absent.json") == {}


@pytest.mark.parametrize("bad", [0, 1, -0.1, 1.5, "high", "NaN"])
def test_a_threshold_the_bands_cannot_be_built_from_is_refused(tmp_path, bad):
    path = write_db(tmp_path, [{"range_id": RANGE_ID, "risk_threshold": bad}])
    with pytest.raises(ValueError, match=str(RANGE_ID)):
        load_range_thresholds(path)


def test_the_shipped_raw_json_is_valid_and_sets_every_range():
    path = PATHS.policy_db
    if not path.exists():
        pytest.skip("no raw.json")
    configured = load_range_thresholds(path)
    ranges = [r["range_id"] for a in json.loads(path.read_text())["accounts"] for r in a["ranges"]]
    assert sorted(configured) == sorted(ranges)


# ------------------------------------------------------------ the forecaster


def test_a_degraded_forecast_still_states_the_threshold_it_was_read_against(tmp_path):
    service = ForecasterService(tmp_path / "missing.pkl")  # no model: degraded
    rows = make_replay(1)["windows"]

    assert service.score_window(rows).decision_threshold == service.decision_threshold
    assert service.score_window(rows, 0.8).decision_threshold == 0.8
    with pytest.raises(ValueError):
        service.score_window(rows, 1.2)


# ---------------------------------------------------------------- the stream


def run(scores, threshold):
    forecaster = ScriptedForecaster(scores, threshold=0.5)
    state = replay_engine.build_state(make_replay(len(scores)))
    events = [replay_engine.step(state, forecaster, threshold) for _ in scores]
    return state, events


def test_a_higher_range_threshold_suppresses_the_warning():
    state, events = run(SCORES, 0.7)
    assert state.first_warning_epoch is None
    assert all(e["forecast"]["decision_threshold"] == 0.7 for e in events)


def test_a_lower_range_threshold_warns_where_the_model_would_not():
    state, _ = run([0.1, 0.3, 0.3, 0.1], 0.25)
    assert state.first_warning_epoch == EPOCH + 20


def test_no_range_threshold_uses_the_model():
    state, events = run(SCORES, None)
    assert state.first_warning_epoch == EPOCH + 20
    assert all(e["forecast"]["decision_threshold"] == 0.5 for e in events)


# ------------------------------------------------------------------- the API


@pytest.fixture
def api():
    return importlib.import_module("dfsi.api.app")


def test_the_default_decision_epoch_follows_the_range_threshold(api, monkeypatch):
    replay = make_replay(len(SCORES))
    monkeypatch.setattr(api, "_forecaster", ScriptedForecaster(SCORES, threshold=0.5))
    monkeypatch.setattr(api.replay_engine, "load_replay", lambda replay_id: replay)

    monkeypatch.setattr(api, "_range_thresholds", {})
    assert api._replay_and_decision_epoch("scripted", None)[1] == EPOCH + 20
    monkeypatch.setattr(api, "_range_thresholds", {RANGE_ID: 0.7})
    assert api._replay_and_decision_epoch("scripted", None)[1] == EPOCH + 50  # never warns
