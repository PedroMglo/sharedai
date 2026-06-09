"""Conservative local fallback policy for consumers."""

from __future__ import annotations

import os
from uuid import uuid4

from sharedai.system.resource_governor.constants import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_LEASE_TTL_SECONDS,
)
from sharedai.system.resource_governor.schemas import (
    DecisionType,
    Lane,
    LeaseDecision,
    LeaseDecisionKind,
    LeaseRequest,
    QualityPolicy,
    UserImpact,
)


def fallback_decision(request: LeaseRequest, *, reason: str = "governor unavailable") -> LeaseDecision:
    """Return a safe decision when the authoritative governor cannot be reached."""
    lease_id = f"local_lease_{uuid4().hex}"
    lane = Lane(request.lane)

    if lane is Lane.INTERACTIVE:
        return LeaseDecision(
            decision=LeaseDecisionKind.GRANTED,
            decision_type=DecisionType.NORMAL,
            lease_id=lease_id,
            ttl_seconds=request.requested_ttl_seconds or DEFAULT_LEASE_TTL_SECONDS,
            heartbeat_interval_seconds=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            reason=f"local fallback: {reason}; interactive allowed",
            effective_quality_policy=QualityPolicy.PRESERVE,
            expected_user_impact=UserImpact.NONE,
        )

    if lane is Lane.INTERACTIVE_ENRICHMENT:
        return LeaseDecision(
            decision=LeaseDecisionKind.SKIP_OPTIONAL,
            decision_type=DecisionType.SOFT_ADVICE,
            reason=f"local fallback: {reason}; optional enrichment skipped",
            retry_after_seconds=10,
            effective_quality_policy=QualityPolicy.PRESERVE,
            expected_user_impact=UserImpact.LOW,
        )

    if lane is Lane.BACKGROUND:
        return LeaseDecision(
            decision=LeaseDecisionKind.GRANTED_WITH_LIMITS,
            decision_type=DecisionType.SOFT_ADVICE,
            lease_id=lease_id,
            ttl_seconds=30,
            heartbeat_interval_seconds=10,
            limits={"workers": 1, "batch_size": 1, "checkpoint_required": True},
            reason=f"local fallback: {reason}; background limited conservatively",
            effective_quality_policy=request.quality_policy,
            expected_user_impact=UserImpact.NONE,
        )

    if lane is Lane.STORAGE:
        return LeaseDecision(
            decision=LeaseDecisionKind.DEFER,
            decision_type=DecisionType.HARD_BLOCK,
            reason=f"local fallback: {reason}; storage pauses until governor is available",
            retry_after_seconds=60,
            effective_quality_policy=QualityPolicy.PRESERVE,
            expected_user_impact=UserImpact.NONE,
        )

    allow_heavy = os.environ.get("AI_RESOURCE_GOVERNOR_ALLOW_HEAVY_GPU_FALLBACK", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if allow_heavy:
        return LeaseDecision(
            decision=LeaseDecisionKind.GRANTED_WITH_LIMITS,
            decision_type=DecisionType.SOFT_ADVICE,
            lease_id=lease_id,
            ttl_seconds=30,
            heartbeat_interval_seconds=10,
            limits={"concurrency": 1, "checkpoint_required": True},
            reason=f"local fallback: {reason}; heavy GPU allowed by explicit env override",
            effective_quality_policy=request.quality_policy,
            expected_user_impact=UserImpact.MEDIUM,
        )
    return LeaseDecision(
        decision=LeaseDecisionKind.DENY,
        decision_type=DecisionType.HARD_BLOCK,
        reason=f"local fallback: {reason}; heavy GPU denied without governor",
        retry_after_seconds=30,
        effective_quality_policy=QualityPolicy.PRESERVE,
        expected_user_impact=UserImpact.NONE,
    )
