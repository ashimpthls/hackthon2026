"""Per-range decision thresholds, configured in raw.json.

The model is calibrated to one decision threshold across every range. A range
in raw.json may set its own `risk_threshold` instead: a noisy range held to a
higher bar, a critical one warned about sooner. A range without one falls back
to the model's calibrated threshold.

The threshold only changes how a score is *read* (its band, and whether it
counts towards a warning), never the score itself. It is read once, when the
API starts; edit raw.json and restart to change it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..rehearsal.policy_db import load_policy_db

log = logging.getLogger("dfsi.forecaster.range_thresholds")


def load_range_thresholds(path: Path) -> dict[int, float]:
    """`range_id -> risk_threshold` for every range in raw.json that sets one.

    A missing file means no overrides. The file is validated as a whole by
    `policy_db.load_policy_db`, which raises `PolicyDbError` (a `ValueError`)
    naming the file and range: silently scoring a range on the model's
    threshold when the file asked for another would misreport that range's
    warnings.
    """
    data = load_policy_db(path)
    if data is None:
        log.warning("no policy db at %s; every range uses the model's threshold", path)
        return {}
    return {
        int(rng["range_id"]): float(rng["risk_threshold"])
        for account in data["accounts"]
        for rng in account["ranges"]
        if rng.get("risk_threshold") is not None
    }
