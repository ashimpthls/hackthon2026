"""Reading and validating `raw.json`, the policy database.

Two things read the file at startup: the policy store (the configured lines per
range and traffic type) and the per-range risk thresholds. Each used to parse it
on its own and trust what it found. That was fine for the file as shipped and
dangerous for any hand edit, because a bad value does not fail, it quietly
changes what every replay means:

* a policy line of 0 (or less) makes the wall block *all* traffic and every
  window "over threshold", so a completely calm replay declares an attack in
  358 of its 360 windows;
* a missing key surfaced as a bare `KeyError` that named neither the file nor
  the range;
* a repeated `range_id` silently kept the last copy;
* malformed JSON crashed the API at import without saying which file.

`load_policy_db` is now the one place the file is read. It checks the whole file
and reports *every* problem at once, each named by file, range and traffic type,
so a bad edit is fixed in one pass. It raises `PolicyDbError` (a `ValueError`)
and the API refuses to start: a policy that was asked for and not applied would
misreport every warning on that range, so failing loudly is the safe answer.

A missing file is not an error; callers decide what "no policy database" means.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

#: Bytes per packet, used to convert a pps line to Mbps when a type does not say.
#: Matches the generator's assumptions.
DEFAULT_PACKET_BYTES = {"udp": 1100, "tcp_syn": 64, "http": 700}

#: How far a line's Mbps may sit from pps x packet size before the file is
#: called inconsistent. The two are derived from each other, so this is slack for
#: rounding in a hand edit, not a tolerance for real disagreement.
MBPS_TOLERANCE = 0.01


class PolicyDbError(ValueError):
    """`raw.json` is unreadable or does not describe a usable policy database."""


def _number(value) -> float | None:
    """`value` as a finite float, or `None` (booleans are not numbers here)."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) and number == int(number) else None


def validate_policy_db(data) -> list[str]:
    """Every problem in a parsed policy database, each naming where it is.

    An empty list means the file is usable. A range with no (or empty)
    `traffic_types` is valid: it may exist only to carry a `risk_threshold`.
    """
    if not isinstance(data, dict):
        return [f"the top level must be an object, got {type(data).__name__}"]
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        return ["'accounts' must be a list"]

    problems: list[str] = []
    seen: dict[int, str] = {}
    for a_index, account in enumerate(accounts):
        where = f"accounts[{a_index}]"
        if not isinstance(account, dict):
            problems.append(f"{where} must be an object")
            continue
        if _integer(account.get("account_id")) is None:
            problems.append(f"{where}: 'account_id' must be an integer, got {account.get('account_id')!r}")
        ranges = account.get("ranges")
        if not isinstance(ranges, list):
            problems.append(f"{where}: 'ranges' must be a list")
            continue
        for r_index, rng in enumerate(ranges):
            if not isinstance(rng, dict):
                problems.append(f"{where}.ranges[{r_index}] must be an object")
                continue
            range_id = _integer(rng.get("range_id"))
            if range_id is None:
                problems.append(f"{where}.ranges[{r_index}]: 'range_id' must be an integer, got {rng.get('range_id')!r}")
                continue
            label = f"range {range_id}"
            here = f"{where}.ranges[{r_index}]"
            if range_id in seen:
                problems.append(f"{label}: listed twice ({seen[range_id]} and {here}); only one copy would be used")
            seen[range_id] = here
            problems.extend(_validate_range(label, rng))
    return problems


def _validate_range(label: str, rng: dict) -> list[str]:
    problems: list[str] = []

    if rng.get("risk_threshold") is not None:
        threshold = _number(rng["risk_threshold"])
        if threshold is None or not 0.0 < threshold < 1.0:
            problems.append(
                f"{label}: invalid risk_threshold {rng['risk_threshold']!r}; "
                "it must be a number strictly between 0 and 1 (omit it to use the model's)"
            )

    types = rng.get("traffic_types", {})
    if not isinstance(types, dict):
        return problems + [f"{label}: 'traffic_types' must be an object"]
    for traffic_type, spec in types.items():
        where = f"{label}/{traffic_type}"
        if not isinstance(spec, dict):
            problems.append(f"{where}: must be an object")
            continue
        lines = {}
        for key in ("max_threshold_pps", "max_threshold_mbps"):
            if key not in spec:
                problems.append(f"{where}: missing '{key}'")
                continue
            value = _number(spec[key])
            if value is None or value <= 0:
                problems.append(f"{where}: {key} must be a positive number, got {spec[key]!r}")
            else:
                lines[key] = value
        packet = spec.get("mean_packet_bytes", DEFAULT_PACKET_BYTES.get(traffic_type))
        if "mean_packet_bytes" in spec and (_integer(packet) is None or packet <= 0):
            problems.append(f"{where}: mean_packet_bytes must be a positive integer, got {packet!r}")
        elif len(lines) == 2 and packet:
            expected = lines["max_threshold_pps"] * packet * 8 / 1_000_000
            if abs(lines["max_threshold_mbps"] - expected) > MBPS_TOLERANCE * expected:
                problems.append(
                    f"{where}: max_threshold_mbps {lines['max_threshold_mbps']:g} disagrees with "
                    f"max_threshold_pps x {packet} bytes, which gives {expected:g}"
                )
    return problems


def load_policy_db(path: Path) -> dict | None:
    """Read and validate the policy database. `None` if the file does not exist.

    Raises `PolicyDbError`, naming the file, for malformed JSON or for any
    problem `validate_policy_db` finds.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PolicyDbError(
            f"{path} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    problems = validate_policy_db(data)
    if problems:
        raise PolicyDbError(
            f"{path} is not a valid policy database ({len(problems)} problem"
            f"{'s' if len(problems) != 1 else ''}):\n  - " + "\n  - ".join(problems)
        )
    return data
