# sharedai Operating Spec

`sharedai` is an independently published package for neutral ai-local helpers and contracts.

Own here:

- Servicekit auth helpers and health/capabilities contracts.
- Shared LLM contracts, payload builders, URL validation, token estimation, and small HTTP transport helpers.
- Shared evidence contracts and report formatting helpers.
- External Resource Governor client contracts and typed client wrappers.
- Serialization, validation, and other domain-neutral glue needed by multiple services.

Do not own here:

- Storage lifecycle, scratch policy, archive/restore, publication, managed paths, or storage intent parsing.
- Feature-specific parsers, pipelines, APIs, adapters, caches, or probes.
- Agent-specific prompt/task behavior.
- Orchestrator routing, policy, reducers, event ledger, tool execution, lifecycle, or fallback behavior.
- RAG ingestion/retrieval/enrichment internals.

Boundary rule:

- A helper belongs in `sharedai` only when at least two project owners can use it without importing domain semantics from each other.
- A service-specific client belongs in the service owner unless it is a neutral typed client for a shared infrastructure service.
- Consumers must import `sharedai` as a package dependency. Do not require a manual clone of this repo inside another repo.
- If a shared helper starts making policy or domain decisions for one owner, migrate it out of `sharedai` and into that owner.
