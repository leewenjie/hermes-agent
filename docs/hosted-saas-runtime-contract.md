# Hosted SaaS runtime contract

This document defines the runtime-facing half of the v1 two-repo hosted SaaS architecture.

It assumes the product is shipped from:

- `hermes-webui`
- `hermes-agent`

with `oxaide` postponed from the critical runtime path.

## Role of `hermes-agent`

`hermes-agent` remains the runtime backend.

It owns:

- model execution
- tool execution
- browser and terminal capabilities
- delegated and background work
- runtime session lifecycle
- hosted runtime policy enforcement
- execution usage and metering

It does not own:

- browser login UX
- Stripe checkout UX
- customer billing shell
- public product/account pages

## Public vs internal API boundary

For hosted SaaS v1:

- browser-facing product APIs live in `hermes-webui`
- runtime-facing internal APIs live in `hermes-agent`

The browser should not depend on raw runtime routes directly.

## Hosted namespace

Add hosted runtime routes under `/api/hosted/*`.

Recommended families:

- `/api/hosted/runtime/*`
- `/api/hosted/policy/*`
- `/api/hosted/usage/*`

## Runtime bootstrap contract

### Endpoint

- `POST /api/hosted/runtime/bootstrap`

### Purpose

Accept a trusted payload from `hermes-webui` describing the authenticated user, workspace, plan, entitlements, and runtime limits.

### Required input concepts

- user identity
- org identity
- workspace identity
- WebUI session identifier
- hosted profile/runtime selection
- plan and entitlements
- runtime limits
- trust/auth context proving the request came from the hosted app layer

### Example input

```json
{
  "user_id": "usr_123",
  "org_id": "org_123",
  "workspace_id": "ws_123",
  "workspace_slug": "acme-prod",
  "session_id": "webui_session_123",
  "profile_id": "hosted-default",
  "plan": "growth",
  "entitlements": {
    "terminal": true,
    "browser": true,
    "file_upload": true,
    "delegation": true,
    "background_jobs": true
  },
  "limits": {
    "max_runtime_seconds": 1800,
    "max_concurrent_jobs": 3,
    "max_upload_bytes": 52428800,
    "monthly_token_budget": 20000000
  },
  "identity": {
    "email": "user@example.com",
    "display_name": "Lee"
  },
  "auth_context": {
    "issuer": "webui",
    "signed_runtime_token": "..."
  }
}
```

### Example output

```json
{
  "runtime_session_id": "rt_123",
  "status": "ready",
  "policy_version": "v1",
  "effective_toolsets": ["file", "terminal", "search", "web"],
  "usage_snapshot": {
    "tokens_used": 0,
    "tool_calls": 0
  }
}
```

## Runtime session lifecycle APIs

Recommended internal routes:

- `POST /api/hosted/runtime/sessions`
- `GET /api/hosted/runtime/sessions/:id`
- `POST /api/hosted/runtime/sessions/:id/pause`
- `POST /api/hosted/runtime/sessions/:id/resume`
- `POST /api/hosted/runtime/sessions/:id/kill`

These represent runtime process/session operations only.

Current implementation status:

- these routes now exist behind the hosted shared-secret boundary
- session create/bootstrap persists a durable hosted runtime session record
- session get/list/pause/resume/kill operate on that durable runtime lifecycle state
- the lifecycle state machine is scaffolded and authoritative for hosted-session control posture
- it is not yet bound to full live agent execution control

## Policy contract

Hosted runtime policy should be enforced at execution time, not only in UI.

Recommended routes:

- `POST /api/hosted/runtime/policy/evaluate`
- `POST /api/hosted/runtime/tool-access/check`

These can also be folded into session bootstrap if the implementation stays simpler.

### Policy inputs should cover

- tool access flags
- browser/terminal/file permissions
- delegation/background capability
- runtime timeouts
- concurrency caps
- usage/budget caps
- network restrictions if needed

## Usage and metering contract

Recommended routes:

- `GET /api/hosted/runtime/usage/session/:id`
- `POST /api/hosted/runtime/usage/report`
- `GET /api/hosted/runtime/health`

The runtime is authoritative for:

- token usage
- tool usage
- wall-clock runtime
- background/delegation counts
- failures and policy denials

## What `hermes-agent` should not absorb for v1

Do not move these concerns into the runtime backend:

- Supabase browser login flows
- Stripe checkout orchestration meant for the user-facing workspace app
- billing portal UX
- account/customer settings UI
- public commercial product APIs

Those belong in `hermes-webui` for the two-repo v1 model.

## Why this runtime split exists

Keeping `hermes-agent` narrow preserves:

- cleaner execution boundaries
- simpler runtime security review
- easier future extraction if a third control plane is added later
- less risk of billing/account logic breaking agent execution paths

## Cloudflare-facing note

For the first hosted deployment:

- `hermes-webui` is the public host
- `hermes-agent` can remain internal or otherwise restricted
- `hermes-agent` should trust only signed/validated requests from the hosted app layer

This keeps the deployment simple without turning the runtime into a public SaaS monolith.