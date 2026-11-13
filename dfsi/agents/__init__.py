"""Explainers — two bounded agents plus a code validator ("the first mate")."""

from .orchestrator import AgentResult, ExplainerOrchestrator
from .tools import ADVISOR_TOOLS, INVESTIGATOR_TOOLS, EvidenceBundle, ReadOnlyToolbox
from .validator import ValidationReport, monitoring_only_notice, validate

__all__ = [
    "ADVISOR_TOOLS",
    "INVESTIGATOR_TOOLS",
    "AgentResult",
    "EvidenceBundle",
    "ExplainerOrchestrator",
    "ReadOnlyToolbox",
    "ValidationReport",
    "monitoring_only_notice",
    "validate",
]
