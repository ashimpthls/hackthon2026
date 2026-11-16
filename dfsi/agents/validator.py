"""The validator — code, not a model.

Every agent output passes through here before a human sees it. The validator
is deliberately boring, deterministic and written in plain Python: it is the
component that must not be able to hallucinate.

Five checks, from the safety design:

1. **tenant scope**   — the output may only discuss the requested account/range
2. **output format**  — required fields present, correct types, bounded length
3. **freshness**      — the evidence is recent enough to still be true
4. **evidence**       — every number cited appears in the evidence bundle
5. **allowed actions** — only the four fixed candidates, never an executed one

A failure is never silently repaired. The output is replaced with a
monitoring-only notice that states what failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..config import STATE_MONITORING_ONLY, WINDOW_SECONDS
from ..rehearsal.simulator import CANDIDATES_BY_KEY

#: Oldest evidence the validator will let through, in seconds.
MAX_EVIDENCE_AGE_SECONDS = 90

#: Phrases that would mean the agent is claiming to have *done* something.
_ACTION_CLAIM_PATTERNS = (
    r"\bI (?:have )?(?:applied|enabled|blocked|diverted|deployed|executed|activated)\b",
    r"\bpolicy (?:has been|was) (?:applied|changed|updated|deployed)\b",
    r"\btraffic (?:has been|was) (?:blocked|diverted|dropped)\b",
    r"\bI (?:will|'ll) (?:apply|enable|block|divert|deploy|execute)\b",
    r"\bautomatically (?:applied|mitigated|blocked)\b",
)

#: The agents explain; ML produces the numbers. An agent inventing its own
#: probability is the single most dangerous failure mode in this design.
_RISK_NUMBER_PATTERNS = (
    r"\b(?:risk|probability|likelihood|confidence|chance)\s*(?:score|level|is|of|:|=)?\s*"
    r"(?:is\s*)?(\d{1,3}(?:\.\d+)?)\s*%",
    r"\b(?:risk|probability|likelihood|confidence)\s*(?:score)?\s*(?:is|of|:|=)\s*"
    r"(0?\.\d+|1\.0+)\b",
    r"\bI (?:estimate|calculate|compute|assess) (?:the )?(?:risk|probability)\b",
)

#: Numbers in prose that must be traceable to the bundle. Percentages, decimals
#: and multi-digit integers; bare small integers (1-4 option counts, "two
#: agents") are not treated as claims.
_NUMBER_IN_TEXT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")

#: Names that contain digits but are not claims about this incident: ticket ids
#: ("PPNG-6760") and CIDR prefix lengths ("/24"). The Advisor's own prompt tells
#: it to cite PPNG-6760 and the /24 blast radius, so counting them as invented
#: figures made the briefing that follows the prompt fail validation. Prefix
#: lengths are limited to /8-/32 so an arbitrary "/87" cannot hide a figure.
_REFERENCE_NAMES = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*(?:-\d+)+\b|/(?:[89]|[12]\d|3[0-2])\b")

#: Tolerated tokens that appear in explanatory prose but are not claims about
#: this incident: the horizon, the window size and the option count.
_ALWAYS_ALLOWED_NUMBERS = {"120", "10", "60", "4", "2", "1", "3", "0", "300", "30"}


@dataclass
class ValidationReport:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, check: str, reason: str) -> None:
        self.checks[check] = False
        self.failures.append(reason)
        self.passed = False

    def ok(self, check: str) -> None:
        self.checks.setdefault(check, True)


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_strings(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_iter_strings(item))
        return out
    return []


def check_tenant_scope(
    output: dict, expected_account_id: int | None, expected_range_id: int, report: ValidationReport
) -> None:
    """The output must reference only the account and range under review."""
    stated_range = output.get("range_id")
    if stated_range is not None and int(stated_range) != int(expected_range_id):
        report.fail(
            "tenant_scope",
            f"output references range {stated_range}, expected {expected_range_id}",
        )
        return
    stated_account = output.get("account_id")
    if (
        expected_account_id is not None
        and stated_account is not None
        and int(stated_account) != int(expected_account_id)
    ):
        report.fail(
            "tenant_scope",
            f"output references account {stated_account}, expected {expected_account_id}",
        )
        return
    report.ok("tenant_scope")


def check_output_format(
    output: dict, required_fields: Sequence[str], report: ValidationReport, max_chars: int = 4000
) -> None:
    missing = [field_name for field_name in required_fields if not output.get(field_name)]
    if missing:
        report.fail("output_format", f"missing required fields: {missing}")
        return
    total = sum(len(text) for text in _iter_strings(output))
    if total > max_chars:
        report.fail("output_format", f"output is {total} characters, limit is {max_chars}")
        return
    report.ok("output_format")


def check_freshness(
    evidence_epoch: int, now_epoch: int, report: ValidationReport
) -> None:
    age = now_epoch - evidence_epoch
    if age < -WINDOW_SECONDS:
        report.fail("freshness", f"evidence is {-age}s in the future")
        return
    if age > MAX_EVIDENCE_AGE_SECONDS:
        report.fail(
            "freshness",
            f"evidence is {age}s old, limit is {MAX_EVIDENCE_AGE_SECONDS}s",
        )
        return
    report.ok("freshness")


def check_evidence(
    output: dict, citable: set[str], report: ValidationReport
) -> None:
    """No invented numbers, and no risk score of the agent's own making."""
    texts = _iter_strings(output)
    joined = " ".join(texts)

    for pattern in _RISK_NUMBER_PATTERNS:
        match = re.search(pattern, joined, flags=re.IGNORECASE)
        if match:
            report.fail(
                "evidence",
                f"agent produced its own risk figure: {match.group(0)!r}. "
                "Risk numbers come from the model, never from an agent.",
            )
            return

    # Only the figure scan skips reference names; the risk-figure patterns above
    # still read the full text.
    unsupported: list[str] = []
    for raw in _NUMBER_IN_TEXT.findall(_REFERENCE_NAMES.sub(" ", joined)):
        normalised = raw.rstrip("0").rstrip(".") if "." in raw else raw
        if normalised in _ALWAYS_ALLOWED_NUMBERS or raw in _ALWAYS_ALLOWED_NUMBERS:
            continue
        if normalised in citable or raw in citable:
            continue
        unsupported.append(raw)

    if unsupported:
        report.fail(
            "evidence",
            f"cited numbers not present in the evidence bundle: {sorted(set(unsupported))[:6]}",
        )
        return

    if not output.get("evidence_cited"):
        report.warnings.append("output cites no specific evidence items")
    report.ok("evidence")


def check_allowed_actions(output: dict, report: ValidationReport) -> None:
    """Only the four fixed candidates may be named, and only as proposals."""
    texts = " ".join(_iter_strings(output))
    for pattern in _ACTION_CLAIM_PATTERNS:
        match = re.search(pattern, texts, flags=re.IGNORECASE)
        if match:
            report.fail(
                "allowed_actions",
                f"output claims an action was or will be taken: {match.group(0)!r}. "
                "DFSI is simulation-only; humans decide and apply.",
            )
            return

    for option in output.get("options", []) or []:
        key = option.get("policy_key") if isinstance(option, dict) else None
        if key is not None and key not in CANDIDATES_BY_KEY:
            report.fail("allowed_actions", f"unknown policy option {key!r}")
            return

    recommended = output.get("recommended_policy_key")
    if recommended is not None and recommended not in CANDIDATES_BY_KEY:
        report.fail("allowed_actions", f"unknown recommended policy {recommended!r}")
        return

    report.ok("allowed_actions")


def check_tool_use(
    calls: Sequence[dict], allowed: Sequence[str], report: ValidationReport
) -> None:
    """The agent may only have used the tools its role permits."""
    used = {call.get("tool") for call in calls}
    forbidden = sorted(name for name in used if name and name not in set(allowed))
    if forbidden:
        report.fail("tool_use", f"agent used tools outside its role: {forbidden}")
        return
    report.ok("tool_use")


def validate(
    output: dict,
    *,
    required_fields: Sequence[str],
    expected_range_id: int,
    expected_account_id: int | None,
    citable_numbers: set[str],
    evidence_epoch: int,
    now_epoch: int,
    tool_calls: Sequence[dict] = (),
    allowed_tools: Sequence[str] = (),
) -> ValidationReport:
    """Run all checks. Order matters only for which failure is reported first."""
    report = ValidationReport(passed=True)
    check_output_format(output, required_fields, report)
    check_tenant_scope(output, expected_account_id, expected_range_id, report)
    check_freshness(evidence_epoch, now_epoch, report)
    check_evidence(output, citable_numbers, report)
    check_allowed_actions(output, report)
    if allowed_tools:
        check_tool_use(tool_calls, allowed_tools, report)
    return report


def monitoring_only_notice(reason: str, failures: Sequence[str] = ()) -> dict:
    """The fail-safe output. Never says "low risk"; always says "unknown"."""
    return {
        "state": STATE_MONITORING_ONLY,
        "headline": "Monitoring only — explanation withheld",
        "summary": (
            "The explanation could not be verified, so DFSI is reporting "
            "monitoring-only rather than a risk assessment. Treat the "
            "situation as unknown, not as safe."
        ),
        "reason": reason,
        "validator_failures": list(failures),
        "evidence_cited": [],
        "options": [],
        "recommended_policy_key": None,
        "requires_human_decision": True,
    }
