"""
Cloudflare Gateway Bootstrap — patches the Hermes gateway for CF Containers.

Import once at gateway startup. Detects CF environment via env vars and:
  1. Activates R2 storage adapter (skills, memory, artifacts)
  2. Starts CF-native health check HTTP endpoint
  3. Configures scale-to-zero integration
  4. Switches to JSON logging for Cloudflare Logpush

Usage:
    import cloudflare.gateway_bootstrap
    cloudflare.gateway_bootstrap.patch()
Or via env: HERMES_CF_BOOTSTRAP=1
"""
from __future__ import annotations
import logging, os, sys, threading, json
from pathlib import Path

logger = logging.getLogger(__name__)
_patched = False

def is_cf_environment() -> bool:
    return any(os.environ.get(v, "").strip() for v in (
        "HERMES_CF_CONNECTOR_URL", "HERMES_R2_ENDPOINT", "HERMES_SCALE_TO_ZERO"))

def patch() -> bool:
    global _patched
    if _patched: return True
    if not is_cf_environment(): return False
    logger.info("Cloudflare environment detected — applying patches")
    _patch_r2_storage()
    _patch_health_endpoint()
    _patch_scale_to_zero()
    _patch_logging()
    _patched = True
    logger.info("Cloudflare patches applied")
    return True

def _patch_r2_storage():
    try:
        from cloudflare.r2_storage import patch_skill_loader_for_r2
        patch_skill_loader_for_r2()
    except ImportError: pass

def _patch_health_endpoint():
    port = int(os.environ.get("HERMES_HEALTH_PORT", "9119"))
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2)
        return
    except Exception: pass
    try:
        from aiohttp import web
    except ImportError:
        logger.warning("aiohttp not available — health server skipped")
        return
    import asyncio

    async def health(_req): return web.json_response({"status": "ok", "cf": True})
    async def wake(_req):
        logger.info("Wake signal from CF Connector")
        return web.json_response({"status": "waking"})

    def _run():
        app = web.Application()
        app.router.add_get("/api/status", health)
        app.router.add_get("/health", health)
        app.router.add_post("/wake", wake)
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        try: web.run_app(app, host="0.0.0.0", port=port, print=lambda *_: None)
        except Exception: logger.exception("Health server crashed")

    t = threading.Thread(target=_run, daemon=True, name="cf-health"); t.start()
    logger.info("CF health server on port %d", port)

def _patch_scale_to_zero():
    os.environ.setdefault("HERMES_SCALE_TO_ZERO", "1")

def _patch_logging():
    if os.environ.get("HERMES_CF_JSON_LOGS", "").strip().lower() not in ("1", "true", "yes"): return
    class F(logging.Formatter):
        def format(self, r):
            p = {"ts": self.formatTime(r, "%Y-%m-%dT%H:%M:%S.%fZ"), "level": r.levelname, "logger": r.name, "msg": r.getMessage()}
            if r.exc_info and r.exc_info[0]: p["exc"] = self.formatException(r.exc_info)
            return json.dumps(p, default=str)
    for h in logging.getLogger().handlers: h.setFormatter(F())

if os.environ.get("HERMES_CF_BOOTSTRAP", "").strip() in ("1", "true", "yes"): patch()
