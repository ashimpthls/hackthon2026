"""`raw.json` and the generator must agree, because nothing else makes them.

The replays, the baselines and the model are produced from the ranges hard-coded
in `memory/scenarios.py`. `raw.json` is the policy database the API reads: it
overrides the policy lines (and carries each range's `risk_threshold`) but not
the traffic the replays were generated from. Today the two hold the same numbers
because one was copied from the other; nothing stopped them drifting. A range
edited in one place would silently leave replays, baselines and policy
describing different networks.

Until the generator reads `raw.json` directly, this is the guard.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from dfsi.memory.scenarios import DEFAULT_RANGES, _MEAN_PACKET_BYTES, _POLICY_HEADROOM

SHIPPED = Path(__file__).resolve().parent.parent / "raw.json"
FIX = (
    "raw.json and memory/scenarios.py describe the same ranges and must agree: change "
    "both together (and rebuild Memory), or make the generator read raw.json"
)


def differences(data: dict, ranges=DEFAULT_RANGES) -> list[str]:
    """Every way `data` (a parsed raw.json) disagrees with the generator's ranges."""
    out: list[str] = []
    generator = {r.range_id: r for r in ranges}
    listed: dict[int, dict] = {}
    for account in data["accounts"]:
        for rng in account["ranges"]:
            listed[int(rng["range_id"])] = {**rng, "_account": int(account["account_id"])}

    for range_id in sorted(set(generator) - set(listed)):
        out.append(f"range {range_id} is in the generator but not in raw.json")
    for range_id in sorted(set(listed) - set(generator)):
        out.append(f"range {range_id} is in raw.json but not in the generator, so it has no replays")

    meta = data.get("_meta", {})
    if meta.get("policy_headroom_multiplier") is not None and meta["policy_headroom_multiplier"] != _POLICY_HEADROOM:
        out.append(f"policy headroom: raw.json {meta['policy_headroom_multiplier']} vs generator {_POLICY_HEADROOM}")

    for range_id in sorted(set(generator) & set(listed)):
        g, r = generator[range_id], listed[range_id]
        if r["_account"] != g.account_id:
            out.append(f"range {range_id}: account {r['_account']} vs generator {g.account_id}")
        if r.get("range_type") != g.range_type:
            out.append(f"range {range_id}: range_type {r.get('range_type')!r} vs generator {g.range_type!r}")
        types = r.get("traffic_types", {})
        for traffic_type in sorted(set(g.base_pps) ^ set(types)):
            out.append(f"range {range_id}: traffic type {traffic_type} is not in both")
        for traffic_type in sorted(set(g.base_pps) & set(types)):
            spec, where = types[traffic_type], f"range {range_id}/{traffic_type}"
            checks = {
                "base_pps": (spec.get("base_pps"), g.base_pps[traffic_type]),
                "max_threshold_pps": (spec.get("max_threshold_pps"), g.policy_pps(traffic_type)),
                "max_threshold_mbps": (spec.get("max_threshold_mbps"), g.policy_mbps(traffic_type)),
                "mean_packet_bytes": (spec.get("mean_packet_bytes"), _MEAN_PACKET_BYTES[traffic_type]),
            }
            for key, (in_file, in_generator) in checks.items():
                if in_file is None or abs(float(in_file) - float(in_generator)) > 1e-6:
                    out.append(f"{where}: {key} {in_file} vs generator {in_generator}")
    return out


def shipped() -> dict:
    return json.loads(SHIPPED.read_text())


def test_the_shipped_raw_json_describes_the_same_ranges_as_the_generator():
    assert differences(shipped()) == [], FIX


def tamper(change) -> dict:
    data = copy.deepcopy(shipped())
    change(data)
    return data


def test_the_guard_notices_a_changed_base_rate():
    data = tamper(lambda d: d["accounts"][0]["ranges"][0]["traffic_types"]["udp"].update(base_pps=15_000.0))
    assert any("range 90110001/udp: base_pps" in line for line in differences(data))


def test_the_guard_notices_a_changed_policy_line():
    data = tamper(lambda d: d["accounts"][0]["ranges"][0]["traffic_types"]["http"].update(max_threshold_pps=1.0))
    assert any("http: max_threshold_pps" in line for line in differences(data))


def test_the_guard_notices_a_range_added_to_raw_json_alone():
    def add(d):
        extra = copy.deepcopy(d["accounts"][0]["ranges"][0])
        extra["range_id"] = 90110099
        d["accounts"][0]["ranges"].append(extra)

    assert any("90110099 is in raw.json but not in the generator" in l for l in differences(tamper(add)))


def test_the_guard_notices_a_range_dropped_from_raw_json():
    data = tamper(lambda d: d["accounts"][0]["ranges"].pop(0))
    assert any("is in the generator but not in raw.json" in line for line in differences(data))


def test_the_guard_notices_a_changed_headroom_and_account():
    data = tamper(lambda d: (d["_meta"].update(policy_headroom_multiplier=4.0), d["accounts"][0].update(account_id=7)))
    found = " | ".join(differences(data))
    assert "policy headroom" in found and "account 7" in found
