"""Read-only tools the Explainer agents are allowed to call.

The agents get a deliberately small, read-only surface. Every tool here:

* reads from an evidence bundle that was assembled *before* the agent ran,
* returns plain data the agent must quote rather than recompute,
* cannot mutate anything, reach the network, or touch a customer config.

No tool returns, accepts, or stores an IP address. Source information is
country-concentration and prior-episode counts only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..rehearsal.simulator import CANDIDATES


@dataclass
class EvidenceBundle:
    """Everything the agents are permitted to see for one forecast.

    Assembled by the API from the forecaster's own output. The agents cannot
    add to it, and the validator later checks that every number an agent cites
    appears somewhere in here.
    """

    range_id: int
    account_id: int
    window_start_epoch: int
    risk_score: float | None
    risk_band: str
    model_version: str
    feature_version: str
    horizon_seconds: int
    evidence: list[dict] = field(default_factory=list)
    recent_windows: list[dict] = field(default_factory=list)
    scenario_type_hint: str | None = None
    simulation: dict | None = None
    #: Tenant the request is scoped to. The validator enforces this.
    tenant_account_id: int | None = None

    def citable_numbers(self) -> set[str]:
        """String forms of every number an agent may legitimately quote."""
        values: set[str] = set()

        def add(value: Any) -> None:
            if isinstance(value, bool) or value is None:
                return
            if isinstance(value, (int, float)):
                values.add(f"{float(value):.4f}".rstrip("0").rstrip("."))
                values.add(str(int(round(float(value)))))
                values.add(f"{float(value) * 100:.1f}".rstrip("0").rstrip("."))
                values.add(str(int(round(float(value) * 100))))

        add(self.risk_score)
        add(self.horizon_seconds)
        add(self.range_id)
        add(self.account_id)
        for item in self.evidence:
            # Every figure the evidence tool shows an agent is one it may quote.
            for key in ("value", "importance", "contribution"):
                add(item.get(key))
        for row in self.recent_windows:
            for value in row.values():
                add(value)
        if self.simulation:
            for candidate in self.simulation.get("candidates", []):
                for value in candidate.values():
                    add(value)
        return values


class ReadOnlyToolbox:
    """Bound tool implementations for a single evidence bundle.

    Each public method is exposed to Claude as a tool. Methods are pure reads
    over `bundle`; there is intentionally no setter, no file write and no
    network call anywhere in this class.
    """

    def __init__(self, bundle: EvidenceBundle) -> None:
        self._bundle = bundle
        self.calls: list[dict] = []

    def _record(self, name: str, payload: dict) -> None:
        self.calls.append({"tool": name, "arguments": payload})

    # ------------------------------------------------------------------ #
    def get_forecast_evidence(self) -> str:
        """Return the ranked features behind the current risk score."""
        self._record("get_forecast_evidence", {})
        return json.dumps(
            {
                "range_id": self._bundle.range_id,
                "window_start_epoch": self._bundle.window_start_epoch,
                "risk_band": self._bundle.risk_band,
                "model_version": self._bundle.model_version,
                "feature_version": self._bundle.feature_version,
                "horizon_seconds": self._bundle.horizon_seconds,
                "top_features": self._bundle.evidence,
            }
        )

    def get_recent_traffic(self, max_windows: int = 12) -> str:
        """Return the most recent traffic windows for this range."""
        limit = max(1, min(int(max_windows), 36))
        self._record("get_recent_traffic", {"max_windows": limit})
        return json.dumps({"windows": self._bundle.recent_windows[-limit:]})

    def get_source_profile(self) -> str:
        """Return the country-concentration profile and attack history.

        Country concentration only — no addresses are available to this tool.
        """
        self._record("get_source_profile", {})
        latest = self._bundle.recent_windows[-1] if self._bundle.recent_windows else {}
        return json.dumps(
            {
                "country_concentration_index": latest.get("country_hhi"),
                "country_concentration_change_60s": latest.get("country_hhi_delta_60s"),
                "prior_attack_episodes_on_account": latest.get("account_attack_history"),
                "note": "Country mix only. Raw source addresses are not collected or stored.",
            }
        )

    def get_policy_candidates(self) -> str:
        """Return the four fixed response options and their trade-offs."""
        self._record("get_policy_candidates", {})
        return json.dumps(
            {
                "candidates": [
                    {
                        "key": candidate.key,
                        "name": candidate.name,
                        "description": candidate.description,
                        "operational_cost": candidate.operational_cost,
                        "blast_radius": candidate.blast_radius,
                        "engagement_delay_seconds": candidate.engagement_delay_seconds,
                        "reversible_seconds": candidate.reversible_seconds,
                    }
                    for candidate in CANDIDATES
                ],
                "note": "Describing options only. Selecting one requires human approval.",
            }
        )

    def get_simulation_results(self) -> str:
        """Return the rehearsal-room comparison, if a drill has been run."""
        self._record("get_simulation_results", {})
        if not self._bundle.simulation:
            return json.dumps({"available": False, "reason": "no drill has been run yet"})
        return json.dumps({"available": True, **self._bundle.simulation})


#: Tool names each agent may use. Enforced by the validator, so a model that
#: tries to reach outside its role is caught rather than trusted.
INVESTIGATOR_TOOLS = (
    "get_forecast_evidence",
    "get_recent_traffic",
    "get_source_profile",
)
ADVISOR_TOOLS = (
    "get_forecast_evidence",
    "get_policy_candidates",
    "get_simulation_results",
)
