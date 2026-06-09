"""Constants for the shared Resource Governor contract."""

CONTRACT_VERSION = "resource-governor.v1"

DEFAULT_LEASE_TTL_SECONDS = 60
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 10
DEFAULT_ACTIVITY_TTL_SECONDS = 30

DEFAULT_GOVERNOR_URL = "https://127.0.0.1:8585"
DEFAULT_CLIENT_TIMEOUT_SECONDS = 2.0

LANES = (
    "interactive",
    "interactive_enrichment",
    "background",
    "storage",
    "heavy_gpu",
)

LEASE_SCOPES = (
    "request",
    "session",
    "batch",
    "archive",
    "model_load",
    "background_cycle",
)

RESOURCE_CLASSES = (
    "cpu",
    "ram",
    "vram",
    "io_read",
    "io_write",
    "qdrant_write",
    "model_runtime",
)

CAPABILITIES = (
    "chat_stream",
    "routing",
    "rerank",
    "rag_query",
    "document_etl",
    "embedding_gpu_batch",
    "embedding_cpu_batch",
    "graph_llm",
    "bm25_rebuild",
    "audio_transcribe_gpu",
    "audio_transcribe_cpu",
    "storage_archive",
    "model_warmup",
    "model_load",
    "deep_reasoning_batch",
)
