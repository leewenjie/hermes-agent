# Hermes Agent role in the Oxaide hosted stack

This note documents the intended role of `hermes-agent` inside the hosted Oxaide agents product.

## Purpose

`hermes-agent` is the runtime backend.

It should remain focused on:

- model execution
- tool execution
- terminal and browser capabilities
- runtime orchestration
- delegated and background agent work

It should not become the public SaaS billing or account shell.

## Stack relationship

Within the hosted Oxaide product:

- `hermes-webui` owns the main hosted application shell
- `hermes-agent` owns runtime execution
- `oxaide` owns billing, account shell, and lightweight control-plane duties

## Public boundary

The preferred public browser entry point is the hosted application layer, not the runtime backend directly.

This means:

- browser-facing hosted workspace APIs live in `hermes-webui`
- internal runtime and capability execution live in `hermes-agent`

## Why this matters

Keeping `hermes-agent` narrow avoids turning the runtime into a second product shell.

That keeps billing, entitlements, and commercial logic out of the runtime service while preserving a clean execution boundary for the hosted app.

## Related docs

- `hermes-webui/docs/architecture/oxaide-stack-boundary.md`
- `oxaide/docs/operations/agents-control-plane-contract.md`
- `oxaide/docs/launch/agents-production-cutover.md`
