# sharedai

Shared technical utilities extracted from the Local AI workspace. This package is intentionally limited to stable cross-service infrastructure: security helpers, model registry/token estimates, resource-governor contracts, and storage contracts/clients.

Do not add domain-specific agent logic, prompts, pipelines, generated artifacts, or service-only configuration here.

## Modules

- `sharedai.security`: API security headers, redaction, request IDs, secret loading, and optional FastAPI auth dependency.
- `sharedai.llm`: model registry loading and token estimation helpers.
- `sharedai.system.resource_governor`: shared Resource Governor v1 constants, schemas, fallback policy, client, and effective-policy builder.
- `sharedai.storage`: storage_guardian object contracts and HTTPS client.
