from sharedai.evidence import evidence_metadata
from sharedai.llm import estimate_tokens, strip_think
from sharedai.llm.backend_url import llm_chat_url
from sharedai.system.resource_governor import ResourceGovernorClient
from sharedai.system.resource_governor.schemas import Capability, Lane, LeaseRequest, LeaseScope, ResourceClass


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
