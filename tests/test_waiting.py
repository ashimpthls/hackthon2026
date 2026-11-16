"""What does holding off cost? The rehearsal room prices waiting.

Waiting is simulated by replaying the same option over the same horizon with its
engagement delayed, so a wait of 0 must reproduce the plain drill exactly and
every other wait must be comparable with it.
"""

from __future__ import annotations

import dataclasses

import pytest

from conftest import EPOCH, attack_windows, quiet_windows
from dfsi.rehearsal.simulator import CANDIDATES_BY_KEY, simulate
from dfsi.rehearsal.waiting import (
    DEFAULT_WAITS,
    MAX_WAIT_SECONDS,
    cost_of_waiting,
    validate_waits,
)

#: A little before the flood's ramp crosses the policy line.
DECISION = EPOCH + 50
FLOOD = attack_windows(count=40, onset_index=6)


def result(**kwargs) -> dict:
    return cost_of_waiting(FLOOD, DECISION, **kwargs)


def row(report: dict, key: str, wait: int) -> dict:
    return next(r for r in report["options"][key]["waiting"] if r["wait_seconds"] == wait)


# ------------------------------------------------------------- the waits


@pytest.mark.parametrize("bad", [0, -10, 15, 7, MAX_WAIT_SECONDS + 10, True, 30.0, "30"])
def test_a_wait_that_is_not_whole_windows_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_waits([bad])


def test_waits_are_sorted_and_deduplicated():
    assert validate_waits([90, 30, 30, 60]) == (30, 60, 90)


def test_no_waits_is_an_error():
    with pytest.raises(ValueError, match="at least one"):
        validate_waits([])


# -------------------------------------------------------- the comparison


def test_every_option_but_hold_is_priced_for_every_wait():
    report = result()
    assert set(report["options"]) == {"tighten_vector", "rate_limit_sources", "full_scrub"}
    for option in report["options"].values():
        assert [r["wait_seconds"] for r in option["waiting"]] == list(DEFAULT_WAITS)
    assert report["simulation_only"] is True


def test_deciding_now_is_exactly_the_plain_simulation():
    report = result()
    for key, option in report["options"].items():
        plain = simulate(FLOOD, DECISION, CANDIDATES_BY_KEY[key])
        now = option["decide_now"]
        assert now["attack_packets_blocked_pct"] == plain.attack_packets_blocked_pct
        assert now["legitimate_packets_lost_pct"] == plain.legitimate_packets_lost_pct
        assert now["seconds_over_threshold"] == plain.seconds_over_threshold
        assert now["time_to_contain_seconds"] == plain.time_to_contain_seconds


def test_waiting_costs_suppression_and_saves_collateral():
    """Both sides are real, and they must both be reported."""
    report = result()
    for key in ("tighten_vector", "rate_limit_sources", "full_scrub"):
        later = row(report, key, 60)
        assert later["attack_blocked_pct_points_lost"] > 0, key
        assert later["collateral_pct_points_avoided"] > 0, key


def test_a_longer_wait_never_helps_containment():
    report = result()
    for option in report["options"].values():
        over = [r["extra_seconds_over_threshold"] for r in option["waiting"]]
        lost = [r["attack_blocked_pct_points_lost"] for r in option["waiting"]]
        assert over == sorted(over)
        assert lost == sorted(lost)


def test_each_wait_delays_a_contained_incident_by_exactly_that_wait():
    """Full scrub contains this flood; engaging N seconds later contains it N later."""
    report = result()
    for wait in DEFAULT_WAITS:
        later = row(report, "full_scrub", wait)
        assert later["extra_seconds_to_contain"] == wait
        assert later["extra_seconds_over_threshold"] == wait
        assert later["no_longer_contains"] is False


def test_an_option_that_never_contains_has_no_containment_delay_to_report():
    report = result()
    later = row(report, "tighten_vector", 60)
    assert report["options"]["tighten_vector"]["decide_now"]["time_to_contain_seconds"] is None
    assert later["extra_seconds_to_contain"] is None
    assert later["no_longer_contains"] is False  # it did not contain before either


def test_the_horizon_is_held_fixed():
    """The percentages are over the same windows however long the wait."""
    report = result()
    scrub = CANDIDATES_BY_KEY["full_scrub"]
    fixed = simulate(
        FLOOD, DECISION, dataclasses.replace(scrub, engagement_delay_seconds=scrub.engagement_delay_seconds + 60)
    )
    assert row(report, "full_scrub", 60)["attack_packets_blocked_pct"] == fixed.attack_packets_blocked_pct


def test_a_wait_past_the_recording_collapses_to_no_intervention():
    short = attack_windows(count=24)  # 230s of traffic; a 300s wait engages well after it ends
    report = cost_of_waiting(short, DECISION, waits=[300])
    later = report["options"]["full_scrub"]["waiting"][0]
    assert later["beyond_recorded_traffic"] is True
    assert later["attack_packets_blocked_pct"] == 0.0


def test_a_wait_inside_the_recording_is_not_flagged():
    assert row(result(), "full_scrub", 30)["beyond_recorded_traffic"] is False


# ------------------------------------------------------ nothing to contain


def test_when_nothing_crosses_the_threshold_waiting_is_free():
    report = cost_of_waiting(quiet_windows(count=30), EPOCH + 50)
    assert report["incident_crosses_threshold"] is False
    assert any("nothing to contain" in note for note in report["notes"])
    for option in report["options"].values():
        for waited in option["waiting"]:
            assert waited["extra_seconds_over_threshold"] == 0
            assert waited["no_longer_contains"] is False


def test_a_decision_with_no_windows_after_it_is_rejected():
    with pytest.raises(ValueError):
        cost_of_waiting(FLOOD, EPOCH + 10_000)
