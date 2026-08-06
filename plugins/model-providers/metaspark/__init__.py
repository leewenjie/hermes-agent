"""Muse Spark (Meta) provider profile.

Muse Spark exposes an OpenAI-compatible chat/completions + responses API
via the Llama API https://api.llama.com. We map it as a standard
OpenAI-compatible provider so Hermes can use ``muse-spark-1.2`` / ``muse-spark-1.1``
without custom transport quirks.

Env:
  MUSE_SPARK_API_KEY / METASPARK_API_KEY / LLM_API_KEY (any one suffices)
  MUSE_SPARK_BASE_URL (optional override, defaults to https://api.llama.com/v1)

Fallback models include both generations so the picker degrades gracefully.
"""

from providers import register_provider
from providers.base import ProviderProfile

metaspark = ProviderProfile(
    name="metaspark",
    aliases=("muse-spark", "muse", "spark", "meta-spark", "meta"),
    api_mode="chat_completions",
    env_vars=("MUSE_SPARK_API_KEY", "METASPARK_API_KEY", "LLM_API_KEY", "MUSE_API_KEY"),
    display_name="Muse Spark",
    description="Muse Spark (Meta) — OpenAI-compatible",
    signup_url="https://www.meta.ai/",
    fallback_models=(
        "muse-spark-1.2",
        "muse-spark-1.1",
        "muse-spark-1-2",
        "muse-spark-1-1",
    ),
    base_url="https://api.llama.com/v1",
    default_aux_model="muse-spark-1.2",
)

register_provider(metaspark)

# Keep the older name also registered so hermes model metaspark-* works
# even if user types the legacy prefix.
try:
    from providers import _REGISTRY as _R, _ALIASES as _A  # type: ignore
    for _alias in ("muse-spark-1.2", "muse-spark-1.1", "muse-spark"):
        _A.setdefault(_alias, "metaspark")
except Exception:
    pass
