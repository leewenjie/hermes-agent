# Hermes Agent on Cloudflare — Architecture & Deployment Guide

## Architecture

```
Messaging Platforms (Telegram, Discord, Slack, WhatsApp, Signal, Matrix)
          │ webhooks
          ▼
┌─────────────────────────────────────┐
│  Cloudflare Workers (Edge Layer)     │  ← Global edge, <10ms ACK
│  • Webhook ingestion + validation   │
│  • Normalized MessageEvent routing  │
└────────────┬────────────────────────┘
             │ Queue (durable delivery)
     ┌───────▼────────┐
     │ Durable Objects │  ← Buffers messages, wakes containers
     │ (GatewaySocket) │
     └───────┬────────┘
             │ WebSocket (WSS)
     ┌───────▼────────┐
     │ CF Containers   │  ← Python agent core (scales to zero)
     │ (Agent Runtime) │
     └────────────────┘
```

## Files

| File | Purpose |
|---|---|
| `connector/src/index.ts` | **Worker + GatewaySocket DO** — all webhook routing & outbound |
| `connector/wrangler.toml` | CF deployment config (DOs, Queues, D1, KV, R2) |
| `Dockerfile` | CF Containers-optimized image |
| `cf_relay_transport.py` | Drop-in relay transport for CF Connector |
| `gateway_bootstrap.py` | Gateway startup patch — health endpoint, scale-to-zero |
| `r2_storage.py` | R2/S3 adapter for skills & memory |
| `schema.sql` | D1 database schema |
| `deploy.sh` | Full deployment script |

## Quick Start

```bash
# 1. Set secrets
cd cloudflare && ./deploy.sh secrets

# 2. Deploy edge connector
./deploy.sh connector

# 3. Build & deploy agent runtime
./deploy.sh runtime

# 4. Point webhooks at your Worker URL
# Telegram: https://your-worker.workers.dev/webhook/telegram/<token>
# Discord:  https://your-worker.workers.dev/webhook/discord
# Slack:    https://your-worker.workers.dev/webhook/slack
# WhatsApp: https://your-worker.workers.dev/webhook/whatsapp
```

## Cost (~$16-26/month moderate usage)

- Workers: $0 (free tier 100k req/day)
- Durable Objects: ~$0.50
- Queues: $0 (free tier)
- KV + R2: ~$0.15
- CF Containers (2 vCPU, 4GB, 2h active/day): ~$15-25

## Integration Points

- `gateway/relay/__init__.py` — detects `HERMES_CF_CONNECTOR_URL` and uses `CFRelayTransport`
- `cloudflare/gateway_bootstrap.py` — auto-imported when `HERMES_CF_BOOTSTRAP=1`
- `cloudflare/r2_storage.py` — monkey-patches skill loader for R2
