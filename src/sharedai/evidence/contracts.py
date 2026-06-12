"""Structured evidence metadata shared by deterministic services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

EVIDENCE_CONTRACT_VERSION = "evidence.v1"


class EvidenceContract(BaseModel):
    version: str = EVIDENCE_CONTRACT_VERSION
    provider: str
    mode: str
    workspace: str
    analysis_mode: str | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    finding_count: int = 0
    artifacts: list[Any] = Field(default_factory=list)
    tables: list[Any] = Field(default_factory=list)


def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary")
    if isinstance(summary, dict):
        return summary
    stats = report.get("stats")
    if isinstance(stats, dict):
        return stats
    return {}


def finding_count(report: dict[str, Any]) -> int:
    keys = ("issues", "exposures", "code_findings", "candidates", "recovered_files")
    total = 0
    for key in keys:
        value = report.get(key)
        if isinstance(value, list):
            total += len(value)
    return total


def build_evidence_contract(
    *,
    provider: str,
    mode: str,
    workspace: str | Path,
    report: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = EvidenceContract(
        provider=provider,
        mode=mode,
        workspace=str(workspace),
        analysis_mode=report.get("analysis_mode"),
        policy=report.get("policy", {}),
        summary=report_summary(report),
        finding_count=finding_count(report),
        artifacts=report.get("artifacts", []),
        tables=report.get("tables", []),
    ).model_dump()
    if extra:
        contract.update(extra)
    return contract


def evidence_metadata(
    *,
    provider: str,
    mode: str,
    workspace: str | Path,
    report: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = build_evidence_contract(
        provider=provider,
        mode=mode,
        workspace=workspace,
        report=report,
        extra=extra,
    )
    return {
        "mode": mode,
        "workspace": str(workspace),
        "analysis_mode": evidence.get("analysis_mode"),
        "policy": evidence.get("policy", {}),
        "summary": evidence.get("summary", {}),
        "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
        "evidence": evidence,
    }
