# Oxaide agents runtime production checklist

This runbook covers the private runtime half of the `agents.oxaide.com` production deployment.

It assumes:

- `hermes-webui` is public
- `hermes-agent` is private
- the hosted runtime contract is enforced through the WebUI boundary

## Runtime role

`hermes-agent` is the runtime backend for:

- model execution
- tool execution
- browser and terminal capabilities
- hosted runtime bootstrap
- hosted runtime session lifecycle
- hosted policy evaluation
- usage and metering

It must not become the public browser-facing service for the hosted app.

## Private-only deployment requirements

- Do not publish a public runtime hostname.
- Bind to `127.0.0.1` when colocated with WebUI, or a private network interface when separate.
- Only allow trusted WebUI-originated requests across the hosted runtime boundary.
- Require a real shared secret or equivalent validated trust context.

## Required runtime environment

- `HERMES_HOSTED_RUNTIME_API_HOST=127.0.0.1` or private bind only
- `HERMES_HOSTED_RUNTIME_API_PORT=9001`
- `HERMES_HOSTED_RUNTIME_SHARED_SECRET=<same-secret-as-webui>`
- `HERMES_HOSTED_RUNTIME_TOKEN_ISSUER=hermes-webui`
- `HERMES_HOSTED_RUNTIME_TOKEN_AUDIENCE=hermes-agent`

## Preflight checks

Before letting the public WebUI depend on the runtime, verify:

- the runtime process starts cleanly
- the hosted runtime API is listening on the intended private address
- the hosted health endpoint returns success
- bootstrap requests from WebUI are accepted
- mismatched or missing secrets are rejected

## Required contract checks

Current implementation note:

- the hosted runtime routes are live and secret-protected
- health and bootstrap currently report a scaffolded hosted-runtime state
- bootstrap persists a hosted runtime session record and policy snapshot
- bootstrap is not yet bound to full live agent session creation

### Bootstrap

- `POST /api/hosted/runtime/bootstrap` accepts trusted WebUI payloads
- returned status is healthy or ready
- returned runtime session metadata is usable by WebUI

### Session lifecycle

- session creation succeeds
- session lookup succeeds
- pause, resume, and terminate paths behave as expected if enabled in the current flow

### Policy

- effective tool or capability policy is enforced by the runtime, not only by the UI
- denials are surfaced clearly enough for WebUI to respond safely

### Usage

- usage reporting path is reachable
- usage snapshots are internally coherent for at least one real session

## No-go conditions

Do not approve the runtime for production if any are true:

- runtime is internet-facing
- runtime accepts unauthenticated hosted requests
- runtime secret differs from WebUI secret
- runtime still points at demo or local-only assumptions that do not exist in prod
- WebUI cannot establish a hosted runtime session

## Minimal runtime smoke checklist

- [ ] runtime bound privately only
- [ ] hosted runtime secret configured
- [ ] hosted runtime health returns success
- [ ] bootstrap succeeds from WebUI
- [ ] one runtime-backed session can be created
- [ ] one runtime-backed chat exchange succeeds
- [ ] logs confirm traffic comes from the hosted app layer

## Relationship to public cutover

Do runtime deployment first.

The public WebUI cutover should happen only after:

- private runtime is healthy
- hosted runtime bridge is verified
- one end-to-end session succeeds through the real boundary

That sequence keeps the browser-facing launch from becoming a live runtime debugging session in costume.