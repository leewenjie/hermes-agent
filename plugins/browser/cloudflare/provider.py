"""Cloudflare Browser Run provider using the public CDP REST endpoints."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict

import requests
from urllib.parse import quote

from agent.browser_provider import BrowserProvider

logger = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://api.cloudflare.com/client/v4/accounts"


class CloudflareBrowserProvider(BrowserProvider):
    """Cloudflare Browser Run persistent browser sessions."""

    @property
    def name(self) -> str:
        return "cloudflare"

    @property
    def display_name(self) -> str:
        return "Cloudflare Browser Run"

    def is_available(self) -> bool:
        return bool(
            os.environ.get("CLOUDFLARE_ACCOUNT_ID")
            and os.environ.get("CLOUDFLARE_API_TOKEN")
        )

    def _config(self) -> Dict[str, str]:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not account_id or not api_token:
            raise ValueError(
                "Cloudflare Browser Run requires CLOUDFLARE_ACCOUNT_ID and "
                "CLOUDFLARE_API_TOKEN environment variables."
            )
        return {
            "account_id": account_id,
            "api_token": api_token,
            "base_url": os.environ.get(
                "CLOUDFLARE_BROWSER_API_BASE_URL", _DEFAULT_API_BASE
            ).rstrip("/"),
        }

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config()['api_token']}",
            "Content-Type": "application/json",
        }

    def _endpoint(self) -> str:
        config = self._config()
        return f"{config['base_url']}/{config['account_id']}/browser-rendering/devtools/browser"

    def create_session(self, task_id: str) -> Dict[str, object]:
        try:
            response = requests.post(
                f"{self._endpoint()}?keep_alive=300000",
                headers=self._headers(),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Cloudflare Browser Run connection failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(
                f"Failed to create Cloudflare Browser Run session: "
                f"{response.status_code} {response.text[:500]}"
            )

        payload = response.json()
        result = payload.get("result", payload) if isinstance(payload, dict) else {}
        session_id = str(result.get("sessionId", "")).strip()
        cdp_url = str(result.get("webSocketDebuggerUrl", "")).strip()
        if not session_id or not cdp_url:
            raise RuntimeError("Cloudflare Browser Run returned invalid session metadata")

        session_name = f"hermes_{task_id}_{uuid.uuid4().hex[:8]}"
        return {
            "session_name": session_name,
            "bb_session_id": session_id,
            "cdp_url": cdp_url,
            "features": {"cloudflare_browser_run": True},
        }

    def close_session(self, session_id: str) -> bool:
        try:
            response = requests.delete(
                f"{self._endpoint()}/{quote(session_id, safe='')}",
                headers=self._headers(),
                timeout=10,
            )
            if response.status_code in {200, 201, 204, 404, 410}:
                return True
            logger.warning("Failed to close Cloudflare Browser Run session %s: HTTP %s", session_id, response.status_code)
            return False
        except Exception as exc:
            logger.warning("Exception closing Cloudflare Browser Run session %s: %s", session_id, exc)
            return False

    def emergency_cleanup(self, session_id: str) -> None:
        try:
            self.close_session(session_id)
        except Exception:
            logger.debug("Emergency cleanup failed for Cloudflare session %s", session_id, exc_info=True)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Cloudflare Browser Run",
            "badge": "paid",
            "tag": "Cloudflare-managed persistent CDP browser",
            "env_vars": [
                {
                    "key": "CLOUDFLARE_ACCOUNT_ID",
                    "prompt": "Cloudflare account ID",
                    "url": "https://dash.cloudflare.com",
                },
                {
                    "key": "CLOUDFLARE_API_TOKEN",
                    "prompt": "Cloudflare API token with Browser Rendering - Edit",
                },
            ],
            "post_setup": "agent_browser",
        }