# Oxaide workspace runtime boundary

## Decision

Run one long-lived Hermes container and one persistent volume for each Oxaide workspace seat.

The Oxaide control plane owns authentication, workspace membership, entitlement, billing, per-seat runtime allocation, and routing. Workspace usage can remain pooled for billing, but every authenticated user receives a distinct runtime key and storage boundary. Hermes owns that seat runtime's conversations, tools, files, memories, skills, and completed-turn usage emission.

Do not serve unrelated customer workspaces from one Hermes process or one `/opt/data` volume. Do not treat Hermes profiles as a SaaS security boundary.

## Why

Hermes profiles isolate normal application paths, but profile processes still run as the same Unix user and can see sibling profile directories through terminal access. A shared process also has process-global session, terminal, background-process, and plugin registries. Profiles are useful for trusted operator organization, not hostile tenant isolation.

A seat-dedicated container gives the simplest meaningful boundary:

- one `HERMES_HOME`
- one session database
- one memory and skill tree
- one terminal/filesystem trust domain
- one completed-turn outbox
- one workspace/runtime identity pin
- one authenticated user bound to the runtime allocation
- independent CPU, memory, PID, restart, and deletion controls

## Runtime contract

Provision each runtime with:

- a distinct Compose project or orchestrator allocation
- a dedicated persistent volume mounted at `/opt/data`
- `HERMES_OXAIDE_WORKSPACE_ID` pinned to exactly one workspace
- `HERMES_OXAIDE_RUNTIME_KEY` pinned to exactly one runtime allocation
- `HERMES_OXAIDE_DEMO_AUTH_SECRET` supplied from the secret manager for launch tokens
- `HERMES_OXAIDE_USAGE_SIGNING_SECRET` supplied separately for completed-turn events
- `HERMES_HOSTED_RUNTIME_SHARED_SECRET` supplied from the secret manager
- the model-provider credential required by that runtime
- ingress through the Oxaide runtime router only
- host exposure bound to loopback or a private container network
- explicit CPU, memory, and PID limits

The launch token must use audience `oxaide-hermes-runtime`, match both runtime pins, have a bounded lifetime, and be single-use at the browser handoff. A trusted Oxaide session cannot choose another Hermes profile or arbitrary startup CWD.

All Oxaide secrets must be at least 32 characters and must not use checked-in placeholder forms such as `replace-with-*` or `__REPLACE_WITH_*`. A validated native Oxaide launch-auth configuration can satisfy the non-loopback dashboard gate without inventing an unrelated interactive auth provider. Incomplete, weak, or placeholder native auth still fails closed.

The public `/api/status` response includes the opaque runtime key already present in the hostname and a launch-secret-keyed HMAC-SHA256 workspace fingerprint. It never returns the workspace ID or a shared secret. Provisioners must compare both values during local and routed readiness checks so a healthy runtime for the wrong tenant cannot satisfy provisioning.

## Routing sequence

1. Oxaide authenticates the user and verifies workspace membership.
2. Oxaide authorizes the requested completed turn before runtime work starts.
3. The runtime router finds or starts the container pinned to that workspace and user.
4. Oxaide signs a short-lived, single-use launch token for that workspace/user/runtime tuple.
5. Hermes verifies audience, signature, timestamps, workspace, and runtime key.
6. Hermes creates or resumes a conversation inside the workspace container.
7. Hermes emits `complete` only after a usable assistant answer; failures emit `release`.

Do not send Stripe secrets, price IDs, webhook credentials, subscription mutation authority, or overage policy into Hermes.

Launch and usage signatures are derived with different purpose strings. A key accepted for browser launch must not authenticate completed-turn events.

## Product logout

Logout is coordinated across both origins:

- From Hermes, the runtime clears its Secure cookies and submits a signed two-minute `oxaide-runtime-logout` continuation to Oxaide. Oxaide verifies the user, workspace, and runtime binding before global Supabase sign-out.
- From Oxaide, the shell resolves the authenticated workspace runtime before local sign-out, then visits a signed logout-only runtime command. The runtime clears its cookies and returns to Oxaide signin.

The logout token cannot launch an agent, authorize a turn, or select another workspace. Redirect and return URLs are fixed to Oxaide HTTPS auth paths.

## Conversation and file privacy

The Oxaide control plane uses one container and persistent volume per workspace user. Two members of the same billed workspace therefore have separate `HERMES_HOME`, `state.db`, managed files, terminal process, memories, skills, caches, and runtime secrets.

Do not collapse seat bindings back to one workspace container. Dashboard-level session or file filters are defense in depth, not a replacement for the runtime and OS boundary when terminal tools are enabled.

## Skills

Ship stable product skills in the image under `skills/`. Store workspace-created skills only in that workspace's `/opt/data/skills`. Never mount one writable skills directory across customer containers.

Required research bundle:

- `investment-research` for source-linked research structure and non-advisory framing
- `market-return-analysis` for reproducible distribution artifacts
- `stocks` for bounded, timestamped Yahoo quote, search, adjusted-history, comparison, and crypto-proxy data
- built-in web/search tools for source-linked evidence

Avoid loading unrelated finance-modeling skills by default. Every loaded skill consumes prompt attention, so activate only the workflow needed for the conversation.

## Hosted feature policy

Oxaide tenant runtimes must set both environment pins exactly:

- `HERMES_TUI_TOOLSETS=web,terminal,file,memory,session_search,clarify,delegation,todo,vision`
- `HERMES_TUI_SKILLS=investment-research,market-return-analysis,stocks`

The TUI gateway validates this contract whenever both Oxaide workspace and runtime pins are present. Missing, incomplete, or expanded policies fail agent creation rather than falling back to personal-agent defaults.

The default hosted product intentionally excludes:

- browser automation, because a local browser can reach private container networks
- arbitrary skill management, plugins, and MCP servers, because they expand executable trust
- code execution, because terminal already covers the approved deterministic scripts
- cron jobs, until scheduled model work is workspace-bound and metered
- project switching and Kanban, which are not part of the one-seat research product
- finance-modeling skills, which add large prompt and spreadsheet dependency surfaces

Delegation is capped to two leaf workers, one spawn level, sixteen iterations, and a four-minute child timeout. Orchestrator spawning and subagent auto-approval are disabled. Persistent memory writes require customer approval.

## Deployment

Use `docker-compose.oxaide-workspace.yml` as the local production-shape template. Give every seat runtime a unique project name so Compose creates a distinct `workspace-data` volume. The control plane should retain the workspace/user/runtime/container/volume mapping and delete it only through an explicit retention workflow.

The template binds the runtime to host loopback. Put the runtime router or authenticated reverse proxy on the same host, or replace the host port with a private orchestrator network. Never publish the container directly to the public internet.

## Verification

Before accepting customer traffic:

- launch with missing workspace pin and confirm startup/auth fails closed
- present a token for a different workspace and confirm HTTP 401
- present a token for a different runtime key and confirm HTTP 401
- route a hostname to a different healthy tenant and confirm readiness fails on identity mismatch
- use a placeholder or short launch/control/usage secret and confirm startup or authorization fails closed
- present a wrong audience or overlong token and confirm HTTP 401
- attempt to select a sibling profile and confirm the root profile remains active
- create files and sessions in two seat containers for the same workspace and confirm volumes are disjoint
- stop and restart one container and confirm only its own sessions return
- confirm denied turns do not build an agent or consume model tokens
- confirm completed turns use one immutable event ID across authorize and complete
- confirm a launch key signature is rejected by the completed-turn endpoint
- remove `HERMES_TUI_TOOLSETS` and confirm Oxaide agent creation fails closed
- add `browser`, `code_execution`, an MCP server, or an arbitrary skill and confirm policy validation rejects it
- confirm the three required research skills preload successfully
- sign out from Hermes and confirm both Hermes and Oxaide sessions are gone
- sign out from Oxaide and confirm an existing tenant-runtime cookie is cleared
