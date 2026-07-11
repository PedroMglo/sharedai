# Capability grant receiver contract

Status: public neutral servicekit contract
Schema: `capability-grant-v1`

This module verifies authority produced elsewhere; it never issues authority or
interprets policy, approval, lease, routing, storage, feature, agent, or RAG
semantics.

The compact token is exactly two unpadded base64url segments: canonical UTF-8
JSON claims and a detached Ed25519 signature over those exact claim bytes. JSON
must have no duplicate keys, non-finite numbers, unsupported values, or
non-canonical encoding. `grant_hash` is SHA-256 of canonical claims with only
`grant_hash` removed.

A receiver configures the trusted issuer, key ID, audience, and public key. It
must verify schema, `max_uses=1`, `Ed25519`, time bounds, request hash, exact
transport, and the grant/task/trace/action/attempt/idempotency headers. The
transport model is frozen and forbids extra fields.

A concrete receiver route may additionally configure an exact mapping of
required signed claims. The dependency snapshots and validates that mapping at
construction, then compares every required value by canonical JSON after
signature/hash verification and before redemption. This keeps capability IDs,
owners, permissions, data scopes, operation classes, effects, or other
issuer-owned claims opaque to `sharedai` while allowing the receiver to bind
its route to the authority it actually implements. A missing or inexact claim
denies the request without consuming the one-use grant.

The FastAPI dependency reconstructs GET query parameters or JSON body plus
query parameters into the same canonical value used for hashing. It verifies
any route-specific required claims and then calls one neutral redeem callback.
Only an explicit `True` allows the handler to run; denial, malformed data,
unavailable authority, or absent auth fails closed.

Exact transport reconstruction uses the ASGI `raw_path`, decoded strictly as
ASCII, rather than Starlette's already percent-decoded `request.url.path`.
The issuer and receiver therefore compare the same canonical encoded route
(`/items/item%3Aone`, for example). Missing, non-ASCII, query-bearing or
non-absolute raw paths fail closed; a framework-normalized path is never used
as an authority fallback.

`CapabilityGrantRedemptionRequest` contains no raw body and no broker secret:
only grant ID/hash, token fingerprint, receiver ID, audience, request hash, and
exact transport. Broker URL selection and I/O belong to the consumer. Optional
callback auth headers can be configured separately, including an
`X-Internal-Token` built by `broker_internal_token_headers()`. Receivers should
also pass their immutable identity through that helper; it emits
`X-AI-Local-Capability-Receiver`, allowing the broker to bind a receiver-scoped
credential to the body audience instead of trusting a caller-declared ID.
`AsyncHTTPCapabilityGrantRedeemer` is the optional canonical HTTP adapter: its
base URL, endpoint template, timeout, and TLS verification/CA input are all
explicit, HTTPS is required unless insecure HTTP is deliberately enabled, it
rejects URL credentials, follows no implicit broker route, and accepts only a 2xx JSON
response with `redeemed: true`, the exact grant ID, and a non-empty redemption
event ID. A success response without causal ledger acknowledgement is denied.

Receivers can derive the issuer-compatible key identity from the mounted public
key with `ed25519_public_key_id()` instead of maintaining a second key-ID knob.
`service_token_dependency(..., accept_internal_token=True)` explicitly enables
`X-Internal-Token` on a broker route; the default remains disabled.
The broker should resolve that credential with `internal_service_api_key()` so
generic `API_KEY`/`API_KEY_FILE` values can never substitute for internal
receiver authentication.

Service API tokens and capability grants are separate controls. Services may
compose `service_token_dependency()` with `capability_grant_dependency()`; one
must never silently substitute for the other.
