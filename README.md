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

## Capability grant receiver boundary

`sharedai.servicekit.capability_grants` provides the domain-neutral receiver
half of a single-use capability grant protocol. It verifies a compact Ed25519
token against an explicitly configured issuer, key ID, audience, exact request,
exact transport, time bounds, and task/trace/action/grant headers. A FastAPI
dependency reconstructs the request hash and calls a caller-provided redeem
callback before the route handler can run.

Routes can also provide an exact mapping of required signed claims. Those
opaque values are checked canonically before redemption, so a grant for the
wrong capability or permission is denied without consuming its single use.

The package does not issue grants, decide policy, own replay state, or select a
broker URL. The redeem callback owns that integration. Optional broker auth
headers are supplied explicitly; `broker_internal_token_headers()` can read a
configured token or token file using the existing servicekit secret reader.
This is independent of `service_token_dependency()`, which remains the service
API authentication layer and may be composed on the same route.

See [`src/sharedai/servicekit/CAPABILITY_GRANTS.md`](src/sharedai/servicekit/CAPABILITY_GRANTS.md)
for the wire and dependency contract.
