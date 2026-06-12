# sharedai

Neutral shared contracts and helpers for ai-local services.

`sharedai` is published as an installable package and should be consumed as a
dependency, for example:

```toml
"sharedai @ git+ssh://git@github.com/PedroMglo/sharedai.git@main"
```

## Owns

- `sharedai.servicekit`: service auth helpers and health/capabilities contracts.
- `sharedai.llm`: LLM contracts, payload builders, URL validation, token estimation, and small HTTP helpers.
- `sharedai.evidence`: evidence metadata contracts and report formatting helpers.
- `sharedai.system.resource_governor`: external Resource Governor contracts and typed client.

## Does Not Own

- Storage lifecycle, scratch policy, archive/restore, publication, managed paths, or storage intent parsing.
- Feature-specific parsers, pipelines, APIs, adapters, caches, or probes.
- Agent-specific prompt/task behavior.
- Orchestrator routing, policy, reducers, event ledger, tool execution, lifecycle, or fallback behavior.
- RAG ingestion/retrieval/enrichment internals.

If a helper starts making decisions for a concrete service, move it to that
service owner and keep `sharedai` as contracts/transport/serialization only.
