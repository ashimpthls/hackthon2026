"""`raw.json` is validated once, at load, and every problem is reported together.

The file is hand-edited, and a bad value does not fail, it quietly changes what
every replay means: a policy line of 0 makes the wall block all traffic and every
window "over threshold", so a calm replay declares an attack in 358 of its 360
windows. Before this, a bad edit surfaced as a bare `KeyError`, was silently
ignored (a repeated range), or crashed the API without naming the file.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dfsi.forecaster.range_thresholds import load_range_thresholds
from dfsi.rehearsal.policy_db import (
    DEFAULT_PACKET_BYTES,
    PolicyDbError,
    load_policy_db,
    validate_policy_db,
)
from dfsi.rehearsal.policy_store import PolicyStore

REPO = Path(__file__).resolve().parent.parent
SHIPPED = REPO / "raw.json"


def line(pps: float = 70_000.0, packet: int = 1100) -> dict:
    return {
        "base_pps": pps / 5,
        "max_threshold_pps": pps,
        "max_threshold_mbps": pps * packet * 8 / 1e6,
        "mean_packet_bytes": packet,
    }


def good() -> dict:
    return {
        "accounts": [
            {
                "account_id": 421001,
                "ranges": [
                    {
                        "range_id": 90110001,
                        "range_type": "INFRA_PROTECT_TOPOLOGY_TYPE_RANGE",
                        "risk_threshold": 0.45,
                        "traffic_types": {"udp": line(70_000, 1100), "tcp_syn": line(110_000, 64), "http": line(240_000, 700)},
                    },
                    {"range_id": 90110002, "traffic_types": {"udp": line(45_000, 1100)}},
                ],
            }
        ]
    }


def mutated(change) -> dict:
    data = copy.deepcopy(good())
    change(data)
    return data


def first_range(data: dict) -> dict:
    return data["accounts"][0]["ranges"][0]


def write(tmp_path: Path, content) -> Path:
    path = tmp_path / "raw.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


# ------------------------------------------------------------------ valid files


def test_the_shipped_raw_json_is_valid():
    assert validate_policy_db(json.loads(SHIPPED.read_text())) == []


def test_a_well_formed_file_has_no_problems():
    assert validate_policy_db(good()) == []


def test_a_range_may_exist_only_to_carry_a_risk_threshold():
    """Threshold-only entries are valid: no traffic_types, or an empty object."""
    data = {"accounts": [{"account_id": 1, "ranges": [
        {"range_id": 1, "risk_threshold": 0.7},
        {"range_id": 2, "risk_threshold": 0.6, "traffic_types": {}},
    ]}]}
    assert validate_policy_db(data) == []


@pytest.mark.parametrize("threshold", [None, 0.5, 0.01, 0.99, "0.5"])
def test_a_usable_risk_threshold_or_none_is_accepted(threshold):
    assert validate_policy_db(mutated(lambda d: first_range(d).update(risk_threshold=threshold))) == []


def test_mbps_within_rounding_of_pps_times_packet_size_is_accepted():
    data = mutated(lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_mbps=616.0 * 1.005))
    assert validate_policy_db(data) == []


# ------------------------------------------------------------ each way to be wrong


CASES = [
    # (name, change, text the problem must contain)
    ("zero pps line", lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps=0.0),
     "range 90110001/udp: max_threshold_pps must be a positive number"),
    ("negative pps line", lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps=-5.0),
     "range 90110001/udp: max_threshold_pps must be a positive number"),
    ("zero mbps line", lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_mbps=0),
     "range 90110001/udp: max_threshold_mbps must be a positive number"),
    ("NaN pps line", lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps=float("nan")),
     "range 90110001/udp: max_threshold_pps must be a positive number"),
    ("pps line as text", lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps="lots"),
     "range 90110001/udp: max_threshold_pps must be a positive number"),
    ("pps line as a boolean", lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps=True),
     "range 90110001/udp: max_threshold_pps must be a positive number"),
    ("missing pps line", lambda d: first_range(d)["traffic_types"]["udp"].pop("max_threshold_pps"),
     "range 90110001/udp: missing 'max_threshold_pps'"),
    ("missing mbps line", lambda d: first_range(d)["traffic_types"]["http"].pop("max_threshold_mbps"),
     "range 90110001/http: missing 'max_threshold_mbps'"),
    ("zero packet size", lambda d: first_range(d)["traffic_types"]["udp"].update(mean_packet_bytes=0),
     "range 90110001/udp: mean_packet_bytes must be a positive integer"),
    ("fractional packet size", lambda d: first_range(d)["traffic_types"]["udp"].update(mean_packet_bytes=1100.5),
     "range 90110001/udp: mean_packet_bytes must be a positive integer"),
    ("mbps that disagrees with pps", lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_mbps=999.0),
     "range 90110001/udp: max_threshold_mbps 999 disagrees"),
    ("a traffic type that is not an object", lambda d: first_range(d)["traffic_types"].update(udp=5),
     "range 90110001/udp: must be an object"),
    ("traffic_types that is not an object", lambda d: first_range(d).update(traffic_types=[1]),
     "range 90110001: 'traffic_types' must be an object"),
    ("a repeated range", lambda d: d["accounts"][0]["ranges"].append(copy.deepcopy(first_range(d))),
     "range 90110001: listed twice"),
    ("a range repeated under another account",
     lambda d: d["accounts"].append({"account_id": 2, "ranges": [copy.deepcopy(first_range(d))]}),
     "range 90110001: listed twice"),
    ("a range_id that is not an integer", lambda d: first_range(d).update(range_id="abc"),
     "'range_id' must be an integer"),
    ("a range_id that is missing", lambda d: first_range(d).pop("range_id"),
     "'range_id' must be an integer"),
    ("an account_id that is not an integer", lambda d: d["accounts"][0].update(account_id="x"),
     "'account_id' must be an integer"),
    ("ranges that is not a list", lambda d: d["accounts"][0].update(ranges={}),
     "'ranges' must be a list"),
    ("a range that is not an object", lambda d: d["accounts"][0]["ranges"].append(7),
     "must be an object"),
    ("an account that is not an object", lambda d: d["accounts"].append("oops"),
     "accounts[1] must be an object"),
]
CASES += [
    (f"risk_threshold {bad!r}", lambda d, bad=bad: first_range(d).update(risk_threshold=bad),
     "range 90110001: invalid risk_threshold")
    for bad in (0, 1, -0.1, 1.5, "high", "NaN", float("nan"), True)
]


@pytest.mark.parametrize(("name", "change", "expected"), CASES, ids=[c[0] for c in CASES])
def test_each_mistake_is_reported_with_where_it_is(name, change, expected):
    problems = validate_policy_db(mutated(change))
    assert any(expected in problem for problem in problems), problems


@pytest.mark.parametrize(
    "data",
    [[], "text", 5, None, {}, {"accounts": {}}, {"accounts": None}],
    ids=["list", "string", "number", "null", "empty object", "accounts object", "accounts null"],
)
def test_a_file_of_the_wrong_overall_shape_is_rejected(data):
    assert validate_policy_db(data)


def test_every_problem_is_reported_in_one_pass(tmp_path):
    def three_mistakes(d):
        first_range(d)["traffic_types"]["udp"]["max_threshold_pps"] = 0
        first_range(d)["traffic_types"]["http"].pop("max_threshold_mbps")
        first_range(d)["risk_threshold"] = 2

    with pytest.raises(PolicyDbError) as caught:
        load_policy_db(write(tmp_path, mutated(three_mistakes)))
    message = str(caught.value)
    assert "3 problems" in message
    assert "udp" in message and "http" in message and "risk_threshold" in message
    assert str(tmp_path / "raw.json") in message  # names the file


# ----------------------------------------------------------------------- loading


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_policy_db(tmp_path / "absent.json") is None


@pytest.mark.parametrize("text", ["{ not json", "", "[1, 2"], ids=["garbage", "empty", "truncated"])
def test_malformed_json_names_the_file_and_the_position(tmp_path, text):
    path = write(tmp_path, text)
    with pytest.raises(PolicyDbError, match="not valid JSON") as caught:
        load_policy_db(path)
    assert str(path) in str(caught.value) and "line" in str(caught.value)


def test_the_error_is_a_value_error_so_existing_handlers_still_catch_it():
    assert issubclass(PolicyDbError, ValueError)


def test_a_valid_file_loads_unchanged(tmp_path):
    assert load_policy_db(write(tmp_path, good())) == good()


# ------------------------------------------- both readers go through the same check


def test_the_policy_store_refuses_a_zero_line(tmp_path):
    """The hazard that motivated this: a zero line turns a calm replay into an attack."""
    path = write(tmp_path, mutated(lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps=0)))
    with pytest.raises(PolicyDbError, match="range 90110001/udp"):
        PolicyStore.from_file(path)


def test_the_thresholds_loader_refuses_the_same_file(tmp_path):
    path = write(tmp_path, mutated(lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps=0)))
    with pytest.raises(PolicyDbError, match="range 90110001/udp"):
        load_range_thresholds(path)


def test_both_readers_still_accept_a_good_file_and_a_missing_one(tmp_path):
    path = write(tmp_path, good())
    assert PolicyStore.from_file(path).range_policy(90110001)["udp"].pps == 70_000.0
    assert load_range_thresholds(path) == {90110001: 0.45}
    assert PolicyStore.from_file(tmp_path / "absent.json").snapshot()["ranges"] == []
    assert load_range_thresholds(tmp_path / "absent.json") == {}


def test_the_default_packet_sizes_are_the_generators():
    from dfsi.memory.scenarios import _MEAN_PACKET_BYTES

    assert DEFAULT_PACKET_BYTES == _MEAN_PACKET_BYTES


def test_the_api_refuses_to_start_on_a_bad_policy_db(tmp_path):
    """Loading happens at import, so a bad file must stop the API with a readable message."""
    bad = write(tmp_path, mutated(lambda d: first_range(d)["traffic_types"]["udp"].update(max_threshold_pps=0)))
    env = {**os.environ, "DFSI_POLICY_DB": str(bad), "ANTHROPIC_API_KEY": "", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", "import dfsi.api.app"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode != 0
    assert "is not a valid policy database" in result.stderr
    assert str(bad) in result.stderr and "range 90110001/udp" in result.stderr
