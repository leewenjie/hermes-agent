"""Cloudflare Browser Run cloud-browser plugin."""

from __future__ import annotations

from plugins.browser.cloudflare.provider import CloudflareBrowserProvider


def register(ctx) -> None:
    """Register the Cloudflare Browser Run provider."""
    ctx.register_browser_provider(CloudflareBrowserProvider())