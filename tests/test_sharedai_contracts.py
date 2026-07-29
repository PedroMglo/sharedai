import httpx

from sharedai.evidence import evidence_metadata
from sharedai.llm import estimate_tokens, strip_think
from sharedai.llm.backend_url import llm_chat_url
from sharedai.system.resource_governor import ResourceGovernorClient
from sharedai.system.resource_governor.schemas import (
    ActivityRecord,
    ActivityRequest,
    ActivityType,
    Capability,
    Lane,
    LeaseRequest,
    LeaseScope,
    ResourceClass,
)


def test_llm_helpers_are_package_local() -> None:
    assert estimate_tokens("hello world") >= 1
    assert strip_think("<think>hidden</think>visible") == "visible"
    assert llm_chat_url("https://ollama:11434") == "https://ollama:11434/api/chat"


def test_evidence_metadata_contract() -> None:
    metadata = evidence_metadata(
        provider="example",
        mode="scan",
        workspace="/tmp/project",
        report={"summary": {"ok": True}, "issues": [{"id": "x"}]},
    )

    assert metadata["evidence_contract_version"] == "evidence.v1"
    assert metadata["evidence"]["finding_count"] == 1


def test_resource_governor_fallback_client() -> None:
    request = LeaseRequest(
        idempotency_key="test",
        requester="test",
        component="unit",
        lane=Lane.BACKGROUND,
        lease_scope=LeaseScope.REQUEST,
        resource_class=ResourceClass.CPU,
        capability=Capability.RERANK,
    )
    decision = ResourceGovernorClient(fallback_enabled=True).request_lease(request)

    assert decision.granted


def test_document_ocr_gpu_lease_contract_is_shared() -> None:
    request = LeaseRequest(
        operation_id="document.ocr",
        idempotency_key="document:test",
        requester="extrator",
        component="features/extrator",
        lane=Lane.HEAVY_GPU,
        lease_scope=LeaseScope.BATCH,
        resource_class=ResourceClass.VRAM,
        capability=Capability.DOCUMENT_OCR_GPU,
        estimated_vram_mb=8192,
    )

    assert request.capability == Capability.DOCUMENT_OCR_GPU
    assert request.operation_id == "document.ocr"


def test_resource_governor_activity_client_contract(monkeypatch) -> None:
    request = ActivityRequest(
        idempotency_key="extrator:job:job-1",
        activity_type=ActivityType.BACKGROUND_TASK,
        requester="extrator",
        component="features/extrator",
        capability=Capability.DOCUMENT_ETL,
        request_id="job-1",
        ttl_seconds=90,
    )
    record = ActivityRecord.from_request(request)
    calls: list[tuple[str, str, dict | None]] = []

    def response(method: str, url: str, payload: dict | None) -> httpx.Response:
        calls.append((method, url, payload))
        body = (
            record.model_dump(mode="json")
            if method == "POST"
            else {"status": "released", "activity_id": record.activity_id}
        )
        return httpx.Response(
            200,
            json=body,
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, *, headers, json, timeout: response("POST", url, json),
    )
    monkeypatch.setattr(
        httpx,
        "delete",
        lambda url, *, headers, timeout: response("DELETE", url, None),
    )

    client = ResourceGovernorClient(
        base_url="https://symbiont:8585",
        token="secret",
    )
    created = client.register_activity(request)
    renewed = client.heartbeat_activity(
        created.activity_id,
        requester="extrator",
        request_id="job-1",
    )
    released = client.release_activity(created.activity_id)

    assert created.activity_id == record.activity_id
    assert renewed.activity_id == record.activity_id
    assert released["status"] == "released"
    assert calls == [
        ("POST", "https://symbiont:8585/resources/activity", request.model_dump(mode="json")),
        (
            "POST",
            f"https://symbiont:8585/resources/activity/{record.activity_id}/heartbeat",
            {
                "activity_id": record.activity_id,
                "requester": "extrator",
                "request_id": "job-1",
            },
        ),
        ("DELETE", f"https://symbiont:8585/resources/activity/{record.activity_id}", None),
    ]


def test_activity_client_never_fabricates_fallback_activity() -> None:
    request = ActivityRequest(
        idempotency_key="background:test",
        activity_type=ActivityType.BACKGROUND_TASK,
        requester="test",
        capability=Capability.DOCUMENT_ETL,
        request_id="job-1",
    )

    try:
        ResourceGovernorClient(fallback_enabled=True).register_activity(request)
    except RuntimeError as exc:
        assert "AI_RESOURCE_GOVERNOR_URL" in str(exc)
    else:
        raise AssertionError("background activity must not use a local fallback")
