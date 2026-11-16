"""The Explainers — two bounded agents, orchestrated.

    Investigator  →  attack or flash crowd?      (reads evidence, traffic, sources)
    Advisor       →  what are the options?       (reads candidates, simulation)
    Validator     →  is every claim supportable? (code, not a model)

Both agents run on the Claude API through the SDK's beta Tool Runner: we supply
the tool functions and the SDK drives the request/execute/loop cycle. That is
the right fit here — the compute is local, the tool surface is small and
read-only, and we want no managed sandbox or external state.

If no API key is configured, or the API fails, or Claude refuses, the
deterministic rules in `fallback.py` take over. The pipeline therefore always
produces an answer, and the answer is always validated the same way.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import STATE_MONITORING_ONLY
from . import fallback
from .prompts import ADVISOR_SYSTEM, INVESTIGATOR_SYSTEM
from .tools import (
    ADVISOR_TOOLS,
    INVESTIGATOR_TOOLS,
    EvidenceBundle,
    ReadOnlyToolbox,
)
from .validator import monitoring_only_notice, validate

log = logging.getLogger("dfsi.agents")

MODEL = os.getenv("DFSI_CLAUDE_MODEL", "claude-opus-5")
MAX_TOKENS = 2_048

INVESTIGATOR_REQUIRED = ("reading", "headline", "summary")
ADVISOR_REQUIRED = ("headline", "summary", "options")

#: Structured-output schemas. Forcing the shape removes a whole class of
#: parsing failures and makes the validator's format check meaningful.
INVESTIGATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reading": {
            "type": "string",
            "enum": ["attack", "flash_crowd", "ambiguous"],
            "description": "Your reading of what is happening.",
        },
        "headline": {
            "type": "string",
            "description": "One line an on-call engineer can read in two seconds.",
        },
        "summary": {
            "type": "string",
            "description": (
                "Two to four sentences. What the evidence shows and what it does not. "
                "Quote only values returned by your tools."
            ),
        },
        "evidence_cited": {
            "type": "array",
            "description": "The specific evidence items that drove your reading.",
            "items": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string"},
                    "value": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["feature", "value", "why"],
                "additionalProperties": False,
            },
        },
        "contrary_evidence": {
            "type": "array",
            "description": "Evidence that argues against your reading. Be honest here.",
            "items": {"type": "string"},
        },
    },
    "required": ["reading", "headline", "summary", "evidence_cited", "contrary_evidence"],
    "additionalProperties": False,
}

ADVISOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {
            "type": "string",
            "description": "Why the trade-offs point where they do. Two to four sentences.",
        },
        "options": {
            "type": "array",
            "description": "All four fixed candidates, in the order the tool returned them.",
            "items": {
                "type": "object",
                "properties": {
                    "policy_key": {
                        "type": "string",
                        "enum": ["hold", "tighten_vector", "rate_limit_sources", "full_scrub"],
                    },
                    "name": {"type": "string"},
                    "what_it_buys": {"type": "string"},
                    "what_it_costs": {"type": "string"},
                    "blast_radius": {"type": "string"},
                },
                "required": [
                    "policy_key",
                    "name",
                    "what_it_buys",
                    "what_it_costs",
                    "blast_radius",
                ],
                "additionalProperties": False,
            },
        },
        "recommended_policy_key": {
            "type": "string",
            "enum": ["hold", "tighten_vector", "rate_limit_sources", "full_scrub"],
            "description": "Which option the trade-offs favour. A human still decides.",
        },
        "recommendation_rationale": {"type": "string"},
    },
    "required": [
        "headline",
        "summary",
        "options",
        "recommended_policy_key",
        "recommendation_rationale",
    ],
    "additionalProperties": False,
}


@dataclass
class AgentResult:
    """One agent's output plus the validator's verdict on it."""

    agent: str
    engine: str
    output: dict
    validated: bool
    validator_checks: dict[str, bool] = field(default_factory=dict)
    validator_failures: list[str] = field(default_factory=list)
    validator_warnings: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    model_version: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "engine": self.engine,
            "output": self.output,
            "validated": self.validated,
            "validator": {
                "checks": self.validator_checks,
                "failures": self.validator_failures,
                "warnings": self.validator_warnings,
            },
            "tool_calls": self.tool_calls,
            "model_version": self.model_version,
        }


class ExplainerOrchestrator:
    """Runs the Investigator, then the Advisor, validating each in turn."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = MODEL,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.model = model
        #: Source of "now" for the freshness check when a bundle carries no
        #: clock of its own. Injectable so staleness is testable.
        self._clock = clock
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._client = None
        self.engine = "offline-rules"
        if self._api_key:
            self._client = self._build_client()
            if self._client is not None:
                self.engine = "claude"

    def _build_client(self):
        try:
            from anthropic import Anthropic
        except ImportError:
            log.warning("anthropic SDK not installed; using offline Explainers")
            return None
        try:
            return Anthropic(api_key=self._api_key)
        except Exception as exc:
            log.warning("could not construct Anthropic client (%s); using offline Explainers", exc)
            return None

    # ------------------------------------------------------------------ #
    def explain(self, bundle: EvidenceBundle) -> dict:
        """Run both agents and return the combined, validated briefing."""
        investigator = self.run_investigator(bundle)
        reading = investigator.output.get("reading") if investigator.validated else None
        advisor = self.run_advisor(bundle, reading)

        return {
            "engine": self.engine,
            "range_id": bundle.range_id,
            "account_id": bundle.account_id,
            "window_start_epoch": bundle.window_start_epoch,
            "model_version": bundle.model_version,
            "feature_version": bundle.feature_version,
            "investigator": investigator.to_dict(),
            "advisor": advisor.to_dict(),
            "requires_human_decision": True,
            "state": (
                STATE_MONITORING_ONLY
                if not (investigator.validated and advisor.validated)
                else "EXPLAINED"
            ),
        }

    # ------------------------------------------------------------------ #
    def run_investigator(self, bundle: EvidenceBundle) -> AgentResult:
        return self._run(
            agent="investigator",
            bundle=bundle,
            system=INVESTIGATOR_SYSTEM,
            schema=INVESTIGATOR_SCHEMA,
            allowed_tools=INVESTIGATOR_TOOLS,
            required_fields=INVESTIGATOR_REQUIRED,
            user_turn=(
                "A forecast for range {range_id} on account {account_id} has moved "
                "into the '{band}' band for the next {horizon} seconds.\n\n"
                "Gather your evidence and tell me: is an attack beginning, or is "
                "this a legitimate surge?"
            ).format(
                range_id=bundle.range_id,
                account_id=bundle.account_id,
                band=bundle.risk_band,
                horizon=bundle.horizon_seconds,
            ),
            offline=lambda: fallback.investigate(bundle),
        )

    def run_advisor(self, bundle: EvidenceBundle, reading: str | None) -> AgentResult:
        context = (
            f"The Investigator's reading is: {reading}."
            if reading
            else "The Investigator's reading could not be verified; treat the "
            "situation as unknown."
        )
        return self._run(
            agent="advisor",
            bundle=bundle,
            system=ADVISOR_SYSTEM,
            schema=ADVISOR_SCHEMA,
            allowed_tools=ADVISOR_TOOLS,
            required_fields=ADVISOR_REQUIRED,
            user_turn=(
                f"Range {bundle.range_id} on account {bundle.account_id} has a "
                f"forecast in the '{bundle.risk_band}' band. {context}\n\n"
                "Read the response options and the drill results, then lay out "
                "the trade-offs for the human who has to choose."
            ),
            offline=lambda: fallback.advise(bundle, reading),
        )

    # ------------------------------------------------------------------ #
    def _run(
        self,
        *,
        agent: str,
        bundle: EvidenceBundle,
        system: str,
        schema: dict,
        allowed_tools: tuple[str, ...],
        required_fields: tuple[str, ...],
        user_turn: str,
        offline: Callable[[], dict],
    ) -> AgentResult:
        toolbox = ReadOnlyToolbox(bundle)
        engine = self.engine
        output: dict | None = None

        # Tenant isolation is decided before any model sees the data: once
        # another tenant's rows have been sent out, rejecting the answer is
        # too late.
        violations = bundle.scope_violations()
        if violations:
            log.error("%s not run: evidence bundle spans tenants: %s", agent, violations)
            return self._withheld(
                agent=agent,
                engine="withheld",
                failures=violations,
                checks={"tenant_scope": False},
                warnings=[],
                toolbox=toolbox,
            )

        if self._client is not None:
            try:
                output = self._call_claude(
                    system=system,
                    schema=schema,
                    allowed_tools=allowed_tools,
                    user_turn=user_turn,
                    toolbox=toolbox,
                )
            except _RefusalError as exc:
                log.warning("%s: Claude declined (%s); falling back to rules", agent, exc)
            except Exception as exc:
                log.warning("%s: Claude call failed (%s); falling back to rules", agent, exc)

        if output is None:
            engine = "offline-rules"
            toolbox = ReadOnlyToolbox(bundle)
            # The offline path reads the same bundle, so record the equivalent
            # reads for the audit trail.
            for name in allowed_tools:
                getattr(toolbox, name)()
            output = offline()

        # Validate what the agent actually said. The ids are stamped on only
        # afterwards: stamping them first would hand the tenant-scope check the
        # very values it is meant to verify.
        now_epoch = bundle.as_of_epoch if bundle.as_of_epoch is not None else int(self._clock())
        report = validate(
            output,
            required_fields=required_fields,
            expected_range_id=bundle.range_id,
            expected_account_id=bundle.tenant_account_id or bundle.account_id,
            citable_numbers=bundle.citable_numbers(),
            evidence_epoch=bundle.newest_evidence_epoch(),
            now_epoch=now_epoch,
            tool_calls=toolbox.calls,
            allowed_tools=allowed_tools,
        )

        if not report.passed:
            log.warning("%s output rejected by validator: %s", agent, report.failures)
            return self._withheld(
                agent=agent,
                engine=engine,
                failures=report.failures,
                checks=report.checks,
                warnings=report.warnings,
                toolbox=toolbox,
            )

        output.setdefault("range_id", bundle.range_id)
        output.setdefault("account_id", bundle.account_id)
        output.setdefault("requires_human_decision", True)

        return AgentResult(
            agent=agent,
            engine=engine,
            output=output,
            validated=True,
            validator_checks=report.checks,
            validator_warnings=report.warnings,
            tool_calls=toolbox.calls,
            model_version=self.model if engine == "claude" else None,
        )

    def _withheld(
        self,
        *,
        agent: str,
        engine: str,
        failures: list[str],
        checks: dict[str, bool],
        warnings: list[str],
        toolbox: ReadOnlyToolbox,
    ) -> AgentResult:
        """The fail-safe result: a monitoring-only notice naming what failed."""
        return AgentResult(
            agent=agent,
            engine=engine,
            output=monitoring_only_notice(
                f"the {agent}'s explanation did not pass validation", failures
            ),
            validated=False,
            validator_checks=checks,
            validator_failures=failures,
            validator_warnings=warnings,
            tool_calls=toolbox.calls,
            model_version=self.model if engine == "claude" else None,
        )

    # ------------------------------------------------------------------ #
    def _call_claude(
        self,
        *,
        system: str,
        schema: dict,
        allowed_tools: tuple[str, ...],
        user_turn: str,
        toolbox: ReadOnlyToolbox,
    ) -> dict:
        """Manual tool-use loop over the read-only toolbox.

        A hand-written loop rather than the Tool Runner because the final turn
        must be a *structured* response: we let Claude use tools freely, then
        make one last call with `output_config.format` and `tool_choice: none`
        so the answer comes back schema-conformant.
        """
        tool_definitions = [_tool_definition(name) for name in allowed_tools]
        messages: list[dict] = [{"role": "user", "content": user_turn}]

        for _ in range(6):  # bounded: these agents need two or three reads
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        # Static prompt, so the prefix is cacheable across calls.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                tools=tool_definitions,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
            )
            if response.stop_reason == "refusal":
                raise _RefusalError("model returned stop_reason=refusal")
            if response.stop_reason != "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                break

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                results.append(self._execute_tool(block, toolbox, allowed_tools))
            # All tool_result blocks go back in a single user message.
            messages.append({"role": "user", "content": results})
        else:
            raise RuntimeError("agent exceeded its tool-use budget")

        messages.append(
            {
                "role": "user",
                "content": (
                    "Now give your final answer in the required structure. Use only "
                    "values you read from the tools."
                ),
            }
        )
        final = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=tool_definitions,
            tool_choice={"type": "none"},
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        if final.stop_reason == "refusal":
            raise _RefusalError("model returned stop_reason=refusal on the final turn")

        text = "".join(
            block.text for block in final.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise RuntimeError("model returned no text in its final answer")
        return json.loads(text)

    def _execute_tool(
        self, block, toolbox: ReadOnlyToolbox, allowed_tools: tuple[str, ...]
    ) -> dict:
        """Run one tool call, returning a tool_result block either way.

        A failed tool must still come back as a `tool_result` with
        `is_error: true` — dropping it leaves the conversation malformed.
        """
        name = block.name
        if name not in allowed_tools:
            # Refused, but recorded: the validator fails an agent that reached
            # outside its role, and can only do so for attempts it can see.
            toolbox.record_denied(name, block.input)
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Tool {name!r} is not available to this agent.",
                "is_error": True,
            }
        try:
            handler = getattr(toolbox, name)
            payload = handler(**(block.input or {}))
            return {"type": "tool_result", "tool_use_id": block.id, "content": payload}
        except Exception as exc:
            log.warning("tool %s failed: %s", name, exc)
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Tool failed: {exc}",
                "is_error": True,
            }


class _RefusalError(RuntimeError):
    """Claude declined the request. Not retried — we fall back to rules."""


#: Tool schemas. `strict` is a top-level field on the tool definition, and each
#: schema sets `additionalProperties: false` with an explicit `required` list.
_TOOL_SCHEMAS: dict[str, dict] = {
    "get_forecast_evidence": {
        "name": "get_forecast_evidence",
        "description": (
            "Read the ranked feature values behind the current risk forecast for "
            "this range, plus the model and feature versions that produced it. "
            "These values are the only numbers you may quote."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    "get_recent_traffic": {
        "name": "get_recent_traffic",
        "description": (
            "Read the most recent 10-second traffic windows for this range: rates, "
            "growth, baseline deviation and blocked share."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "max_windows": {
                    "type": "integer",
                    "description": "How many recent windows to return (1-36).",
                }
            },
            "required": ["max_windows"],
            "additionalProperties": False,
        },
    },
    "get_source_profile": {
        "name": "get_source_profile",
        "description": (
            "Read the source-country concentration index, how it changed over the "
            "last minute, and how many prior attack episodes this account has had. "
            "No IP addresses are available."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    "get_policy_candidates": {
        "name": "get_policy_candidates",
        "description": (
            "Read the four fixed response options with their operational cost, "
            "blast radius, engagement delay and how quickly each can be reversed."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    "get_simulation_results": {
        "name": "get_simulation_results",
        "description": (
            "Read the rehearsal-room drill: what each of the four options would "
            "have achieved on this replay, and what it would have cost. Simulated "
            "on recorded traffic; nothing was applied."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
}


def _tool_definition(name: str) -> dict:
    try:
        return _TOOL_SCHEMAS[name]
    except KeyError:
        raise ValueError(f"no tool schema for {name!r}") from None
