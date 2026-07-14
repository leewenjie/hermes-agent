#!/usr/bin/env python3
"""Bundled entry point for the maintained Yahoo stocks implementation."""

from pathlib import Path
import runpy


RELATIVE_IMPLEMENTATION = Path("optional-skills/finance/stocks/scripts/stocks_client.py")
CANDIDATES = [
    Path(__file__).resolve().parents[4] / RELATIVE_IMPLEMENTATION,
    Path("/opt/hermes") / RELATIVE_IMPLEMENTATION,
]
IMPLEMENTATION = next((path for path in CANDIDATES if path.is_file()), CANDIDATES[0])

if not IMPLEMENTATION.is_file():
    raise SystemExit("The bundled stocks implementation is unavailable.")

runpy.run_path(str(IMPLEMENTATION), run_name="__main__")