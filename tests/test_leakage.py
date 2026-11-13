"""Anti-leakage guarantees.

A forecaster that can see the future scores beautifully and is worthless. The
pitch commits to three specific rules, and each has a test here:

* split by whole scenario, never by neighbouring windows;
* `scenario_id` is never a model input;
* no future-derived value reaches the feature vector.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import requires_memory
from dfsi.config import PATHS
from dfsi.forecaster.train import (
    FORBIDDEN_INPUTS,
    assert_no_leakage,
    split_by_scenario,
)
from dfsi.memory.features import FEATURE_COLUMNS, GROUP_COLUMN, LABEL_COLUMN


def test_feature_list_is_clean():
    assert_no_leakage(list(FEATURE_COLUMNS))


def test_label_is_not_a_feature():
    assert LABEL_COLUMN not in FEATURE_COLUMNS
    assert LABEL_COLUMN in FORBIDDEN_INPUTS


def test_scenario_id_is_not_a_feature():
    """It identifies the episode, so it would let the model memorise outcomes."""
    assert GROUP_COLUMN not in FEATURE_COLUMNS
    assert GROUP_COLUMN in FORBIDDEN_INPUTS


def test_scenario_type_is_not_a_feature():
    """The scenario type *is* the answer; it cannot be an input."""
    assert "scenario_type" not in FEATURE_COLUMNS
    assert "scenario_type" in FORBIDDEN_INPUTS


@pytest.mark.parametrize(
    "column",
    ["is_onset", "declared", "range_over_threshold", "blocked_pps", "sustained_windows"],
)
def test_outcome_derived_columns_are_forbidden(column):
    """Each of these is a consequence of the onset, not a precursor of it."""
    assert column in FORBIDDEN_INPUTS
    assert column not in FEATURE_COLUMNS


def test_assert_no_leakage_rejects_a_forbidden_column():
    with pytest.raises(ValueError, match="future-derived or identifying"):
        assert_no_leakage([*FEATURE_COLUMNS, "is_onset"])


def test_assert_no_leakage_names_every_offender():
    with pytest.raises(ValueError) as excinfo:
        assert_no_leakage([*FEATURE_COLUMNS, "is_onset", "declared"])
    message = str(excinfo.value)
    assert "is_onset" in message and "declared" in message


# ------------------------------------------------------------------ split


def synthetic_frame(n_scenarios: int = 20, windows: int = 8) -> pd.DataFrame:
    """Half attack scenarios, half quiet, so both pools can be split."""
    rows = []
    for s in range(n_scenarios):
        has_onset = s % 2 == 0
        for w in range(windows):
            rows.append(
                {
                    GROUP_COLUMN: f"scenario-{s:04d}",
                    LABEL_COLUMN: int(has_onset and w == windows - 2),
                    "window_start_epoch": w * 10,
                }
            )
    return pd.DataFrame(rows)


def test_split_keeps_whole_scenarios_on_one_side():
    """Neighbouring windows are near-identical; splitting between them leaks."""
    frame = synthetic_frame()
    train, test = split_by_scenario(frame, test_size=0.3, random_state=7)
    overlap = set(train[GROUP_COLUMN]) & set(test[GROUP_COLUMN])
    assert overlap == set()
    assert len(train) + len(test) == len(frame)


def test_split_puts_attacks_on_both_sides():
    """A test set with no onsets cannot measure recall."""
    frame = synthetic_frame()
    train, test = split_by_scenario(frame, test_size=0.3, random_state=7)
    assert train[LABEL_COLUMN].max() == 1
    assert test[LABEL_COLUMN].max() == 1


def test_split_is_reproducible():
    frame = synthetic_frame()
    first, _ = split_by_scenario(frame, test_size=0.3, random_state=7)
    second, _ = split_by_scenario(frame, test_size=0.3, random_state=7)
    assert sorted(first[GROUP_COLUMN].unique()) == sorted(second[GROUP_COLUMN].unique())


# -------------------------------------------------- the built feature table


@requires_memory
def test_built_feature_table_has_no_forbidden_input_in_the_vector():
    frame = pd.read_parquet(PATHS.features)
    assert_no_leakage(list(FEATURE_COLUMNS))
    for column in FEATURE_COLUMNS:
        assert column in frame.columns, column


@requires_memory
def test_built_features_are_finite():
    """A NaN or infinity here would silently become a split point in the trees."""
    frame = pd.read_parquet(PATHS.features)
    values = frame[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    assert np.isfinite(values).all()


@requires_memory
def test_label_is_rare_and_present():
    """Onsets are a fraction of a percent; a label that is common is a bug."""
    frame = pd.read_parquet(PATHS.features)
    rate = frame[LABEL_COLUMN].mean()
    assert 0.0 < rate < 0.05, f"positive rate {rate:.4%} is implausible"


@requires_memory
def test_every_labelled_window_sits_inside_the_horizon_before_an_onset():
    """The label's defining property, asserted directly.

    A scenario can contain several onset runs (an attack that ebbs and
    resumes), so the invariant is against *any* onset, not just the first:
    every labelled window must sit strictly between 1 and 12 windows before
    one. A window at or after every onset would mean the label describes the
    present; a window more than 120 seconds ahead would mean it is not a
    120-second forecast.
    """
    frame = pd.read_parquet(PATHS.features)
    onsets = {
        scenario_id: sorted(set(group))
        for scenario_id, group in frame[frame["is_onset"] == 1]
        .groupby(GROUP_COLUMN)["window_start_epoch"]
    }
    labelled = (
        frame[frame[LABEL_COLUMN] == 1][[GROUP_COLUMN, "window_start_epoch"]]
        .drop_duplicates()
    )
    assert not labelled.empty, "no positive labels at all"

    leads: list[int] = []
    for scenario_id, group in labelled.groupby(GROUP_COLUMN):
        scenario_onsets = onsets.get(scenario_id, [])
        assert scenario_onsets, f"{scenario_id} is labelled but has no onset"
        for epoch in group["window_start_epoch"]:
            ahead = [o - epoch for o in scenario_onsets if 0 < o - epoch <= 120]
            assert ahead, (
                f"{scenario_id} window {epoch} is labelled but no onset falls "
                "in the next 120 seconds"
            )
            leads.append(min(ahead))

    assert min(leads) >= 10, "a label cannot point at the current window"
    assert max(leads) <= 120, "a label cannot reach past the forecast horizon"


@requires_memory
def test_declared_requires_the_full_start_duration():
    """The bell waits out the start duration; the label must respect that.

    This is the arithmetic that broke once: the duration frame is counted in
    *windows*, and the feature table has one row per traffic type, so a
    row-counted frame would divide every duration by three.
    """
    from dfsi.config import thresholds_for

    required = thresholds_for().start_duration_windows
    frame = pd.read_parquet(PATHS.features)
    per_instant = frame[
        [GROUP_COLUMN, "window_start_epoch", "range_over_threshold", "declared"]
    ].drop_duplicates()

    for _, group in per_instant.groupby(GROUP_COLUMN):
        group = group.sort_values("window_start_epoch")
        over = group["range_over_threshold"].to_numpy()
        declared = group["declared"].to_numpy()
        for i in np.flatnonzero(declared == 1):
            window = over[max(0, i - required + 1) : i + 1]
            assert window.sum() >= required, (
                "declared without the full start duration over threshold"
            )
