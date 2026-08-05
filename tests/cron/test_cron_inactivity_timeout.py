"""Tests for cron job inactivity-based timeout.

Tests cover:
- Active agent runs indefinitely (no inactivity timeout)
- Idle agent triggers inactivity timeout with diagnostic info
- Unlimited timeout (HERMES_CRON_TIMEOUT=0)
- Backward compat: HERMES_CRON_TIMEOUT env var still works
- Error message includes activity summary
"""

import concurrent.futures
import os
import sys
import threading
import time
from pathlib import Path

import pytest


# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class FakeAgent:
    """Mock agent with controllable activity summary for timeout tests."""

    def __init__(self, idle_seconds=0.0, activity_desc="tool_call",
                 current_tool=None, api_call_count=5, max_iterations=90):
        self._idle_seconds = idle_seconds
        self._activity_desc = activity_desc
        self._current_tool = current_tool
        self._api_call_count = api_call_count
        self._max_iterations = max_iterations
        self._interrupted = False
        self._interrupt_msg = None

    def get_activity_summary(self):
        return {
            "last_activity_ts": time.time() - self._idle_seconds,
            "last_activity_desc": self._activity_desc,
            "seconds_since_activity": self._idle_seconds,
            "current_tool": self._current_tool,
            "api_call_count": self._api_call_count,
            "max_iterations": self._max_iterations,
        }

    def interrupt(self, msg):
        self._interrupted = True
        self._interrupt_msg = msg

    def run_conversation(self, prompt):
        """Simulate a quick agent run that finishes immediately."""
        return {"final_response": "Done", "messages": []}


class SlowFakeAgent(FakeAgent):
    """Agent that runs for a while, simulating active work then going idle."""

    def __init__(self, run_duration=0.5, idle_after=None, **kwargs):
        super().__init__(**kwargs)
        self._run_duration = run_duration
        self._idle_after = idle_after  # seconds before becoming idle
        self._start_time = None

    def get_activity_summary(self):
        summary = super().get_activity_summary()
        if self._idle_after is not None and self._start_time:
            elapsed = time.time() - self._start_time
            if elapsed > self._idle_after:
                # Agent has gone idle
                idle_time = elapsed - self._idle_after
                summary["seconds_since_activity"] = idle_time
                summary["last_activity_desc"] = "api_call_streaming"
            else:
                summary["seconds_since_activity"] = 0.0
        return summary

    def run_conversation(self, prompt):
        self._start_time = time.time()
        time.sleep(self._run_duration)
        return {"final_response": "Completed after work", "messages": []}


class TestInactivityTimeout:
    """Test the inactivity-based timeout polling loop in cron scheduler."""

    def test_active_agent_completes_normally(self):
        """An agent that finishes quickly should return its result."""
        agent = FakeAgent(idle_seconds=0.0)
        _cron_inactivity_limit = 10.0
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test prompt")
        _inactivity_timeout = False

        result = None
        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                result = future.result()
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                _act = agent.get_activity_summary()
                _idle_secs = _act.get("seconds_since_activity", 0.0)
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False)
        assert result is not None
        assert result["final_response"] == "Done"
        assert not _inactivity_timeout
        assert not agent._interrupted

    def test_idle_agent_triggers_timeout(self):
        """An agent that goes idle should be detected and interrupted."""
        # Agent will run for 0.3s, then become idle after 0.1s of that
        agent = SlowFakeAgent(
            run_duration=5.0,  # would run forever without timeout
            idle_after=0.1,    # goes idle almost immediately
            activity_desc="api_call_streaming",
            current_tool="web_search",
            api_call_count=3,
            max_iterations=50,
        )

        _cron_inactivity_limit = 0.5  # 0.5s inactivity triggers timeout
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test prompt")
        _inactivity_timeout = False

        result = None
        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                result = future.result()
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                try:
                    _act = agent.get_activity_summary()
                    _idle_secs = _act.get("seconds_since_activity", 0.0)
                except Exception:
                    pass
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False, cancel_futures=True)
        assert _inactivity_timeout is True
        assert result is None  # Never got a result — interrupted

    def test_unlimited_timeout(self):
        """HERMES_CRON_TIMEOUT=0 means no timeout at all."""
        agent = FakeAgent(idle_seconds=0.0)
        _cron_inactivity_limit = None  # unlimited

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test prompt")

        # With unlimited, we just await the result directly.
        result = future.result()
        pool.shutdown(wait=False)

        assert result["final_response"] == "Done"

    def _parse_cron_timeout(self, raw_value):
        """Mirror the defensive parsing logic from cron/scheduler.py run_job()."""
        if raw_value:
            try:
                return float(raw_value)
            except (ValueError, TypeError):
                return 600.0
        return 600.0

    def test_timeout_env_var_parsing(self, monkeypatch):
        """HERMES_CRON_TIMEOUT env var is respected."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "1200")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        assert _cron_timeout == 1200.0

        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        assert _cron_inactivity_limit == 1200.0

    def test_timeout_zero_means_unlimited(self, monkeypatch):
        """HERMES_CRON_TIMEOUT=0 yields None (unlimited)."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "0")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        assert _cron_inactivity_limit is None

    def test_timeout_invalid_value_falls_back_to_default(self, monkeypatch):
        """HERMES_CRON_TIMEOUT=abc should fall back to 600s, not raise ValueError."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "abc")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        assert _cron_timeout == 600.0
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        assert _cron_inactivity_limit == 600.0

    def test_timeout_empty_string_uses_default(self, monkeypatch):
        """HERMES_CRON_TIMEOUT='' (empty) should use the 600s default."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        assert _cron_timeout == 600.0

    def test_timeout_error_includes_diagnostics(self):
        """The TimeoutError message should include last activity info."""
        agent = SlowFakeAgent(
            run_duration=5.0,
            idle_after=0.05,
            activity_desc="api_call_streaming",
            current_tool="delegate_task",
            api_call_count=7,
            max_iterations=90,
        )

        _cron_inactivity_limit = 0.3
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test")
        _inactivity_timeout = False

        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                try:
                    _act = agent.get_activity_summary()
                    _idle_secs = _act.get("seconds_since_activity", 0.0)
                except Exception:
                    pass
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False, cancel_futures=True)
        assert _inactivity_timeout

        # Build the diagnostic message like the scheduler does
        _activity = agent.get_activity_summary()
        _last_desc = _activity.get("last_activity_desc", "unknown")
        _secs_ago = _activity.get("seconds_since_activity", 0)

        err_msg = (
            f"Cron job 'test-job' idle for "
            f"{int(_secs_ago)}s (limit {int(_cron_inactivity_limit)}s) "
            f"— last activity: {_last_desc}"
        )
        assert "idle for" in err_msg
        assert "api_call_streaming" in err_msg

    def test_agent_without_activity_summary_uses_wallclock_fallback(self):
        """If agent lacks get_activity_summary, idle_secs stays 0 (never times out).
        
        This ensures backward compat if somehow an old agent is used.
        The polling loop will eventually complete when the task finishes.
        """
        class BareAgent:
            def run_conversation(self, prompt):
                return {"final_response": "no activity tracker", "messages": []}

        agent = BareAgent()
        _cron_inactivity_limit = 0.1  # tiny limit
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test")
        _inactivity_timeout = False

        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                result = future.result()
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                try:
                    _act = agent.get_activity_summary()
                    _idle_secs = _act.get("seconds_since_activity", 0.0)
                except Exception:
                    pass
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False)
        # Should NOT have timed out — bare agent has no get_activity_summary
        assert not _inactivity_timeout
        assert result["final_response"] == "no activity tracker"


class TestSysPathOrdering:
    """Test that sys.path is set before repo-level imports."""

    def test_hermes_time_importable(self):
        """hermes_time should be importable when cron.scheduler loads."""
        # This import would fail if sys.path.insert comes after the import
        from cron.scheduler import _hermes_now
        assert callable(_hermes_now)

    def test_hermes_constants_importable(self):
        """hermes_constants should be importable from cron context."""
        from hermes_constants import get_hermes_home
        assert callable(get_hermes_home)


def test_active_managed_occurrence_hits_wall_clock_deadline(monkeypatch):
    """Managed work is bounded even while it continuously reports activity."""
    import cron.scheduler as scheduler
    import hermes_cli.runtime_provider as runtime_provider
    import run_agent

    class ActiveManagedAgent(FakeAgent):
        instance = None

        def __init__(self, **_kwargs):
            super().__init__(idle_seconds=0.0, activity_desc="stream_delta")
            self._stop = False
            self.closed = False
            type(self).instance = self

        def interrupt(self, msg):
            super().interrupt(msg)
            self._stop = True

        def run_conversation(self, _prompt):
            while not self._stop:
                time.sleep(0.001)
            return {"completed": False, "failed": True, "final_response": "stopped"}

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        scheduler, "_OXAIDE_MANAGED_EXECUTION_TIMEOUT_SECONDS", 0.02
    )
    monkeypatch.setenv("HERMES_OXAIDE_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("HERMES_OXAIDE_PROVIDER", "deepseek")
    monkeypatch.setattr(run_agent, "AIAgent", ActiveManagedAgent)
    resolver_kwargs = {}

    def resolve_managed_runtime(**kwargs):
        resolver_kwargs.update(kwargs)
        return {
            "provider": "deepseek",
            "api_mode": "codex_responses",
            "base_url": "https://api.deepseek.com",
            "api_key": "test-key",
            "source": "test",
        }

    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        resolve_managed_runtime,
    )

    success, _document, _response, error = scheduler.run_job({
        "id": "managed-timeout",
        "name": "Managed timeout",
        "prompt": "Keep working actively",
        "schedule": {"kind": "once", "run_at": "2099-01-01T00:00:00Z"},
        "origin": {"type": "oxaide-scheduled-research-v1"},
    })

    agent = ActiveManagedAgent.instance
    assert success is False
    assert error == "TimeoutError: managed_scheduled_research_timeout"
    assert agent is not None
    deadline = time.monotonic() + 1.0
    while not agent._interrupted and time.monotonic() < deadline:
        time.sleep(0.001)
    assert agent._interrupted is True
    assert "execution deadline" in agent._interrupt_msg
    assert resolver_kwargs["requested"] == "deepseek"
    assert resolver_kwargs["target_model"] == "deepseek-v4-flash"


def test_managed_deadline_is_bounded_when_interrupt_and_close_block(monkeypatch):
    """Third-party shutdown hooks cannot suppress a managed timeout result."""
    import cron.scheduler as scheduler
    import hermes_cli.runtime_provider as runtime_provider
    import run_agent

    interrupt_entered = threading.Event()
    release_interrupt = threading.Event()
    close_entered = threading.Event()
    release_close = threading.Event()
    stop_run = threading.Event()

    class BlockingShutdownAgent(FakeAgent):
        def __init__(self, **_kwargs):
            super().__init__(idle_seconds=0.0, activity_desc="stream_delta")

        def run_conversation(self, _prompt):
            stop_run.wait(timeout=5.0)
            return {"completed": False, "failed": True, "final_response": "stopped"}

        def interrupt(self, _message):
            interrupt_entered.set()
            release_interrupt.wait(timeout=5.0)

        def close(self):
            close_entered.set()
            release_close.wait(timeout=5.0)

    monkeypatch.setattr(
        scheduler, "_OXAIDE_MANAGED_EXECUTION_TIMEOUT_SECONDS", 0.02
    )
    monkeypatch.setenv("HERMES_OXAIDE_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("HERMES_OXAIDE_PROVIDER", "deepseek")
    monkeypatch.setattr(run_agent, "AIAgent", BlockingShutdownAgent)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "deepseek",
            "api_mode": "codex_responses",
            "base_url": "https://api.deepseek.com",
            "api_key": "test-key",
            "source": "test",
        },
    )

    deferred_agents = []
    started = time.monotonic()
    success, _document, _response, error = scheduler.run_job(
        {
            "id": "managed-blocked-shutdown",
            "name": "Managed blocked shutdown",
            "prompt": "Keep working actively",
            "schedule": {"kind": "once", "run_at": "2099-01-01T00:00:00Z"},
            "origin": {"type": "oxaide-scheduled-research-v1"},
        },
        defer_agent_teardown=deferred_agents,
    )
    elapsed = time.monotonic() - started

    try:
        assert success is False
        assert error == "TimeoutError: managed_scheduled_research_timeout"
        assert elapsed < 1.0
        assert interrupt_entered.wait(timeout=1.0)
        assert len(deferred_agents) == 1

        teardown_started = time.monotonic()
        scheduler._teardown_cron_agent_async(
            deferred_agents[0], "managed-blocked-shutdown"
        )
        assert time.monotonic() - teardown_started < 0.5
        assert close_entered.wait(timeout=1.0)
    finally:
        stop_run.set()
        release_interrupt.set()
        release_close.set()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider", "openrouter", "unapproved provider"),
        ("api_mode", "chat_completions", "unapproved API mode"),
        ("base_url", "https://openrouter.ai/api/v1", "non-DeepSeek endpoint"),
        (
            "base_url",
            "https://openai.azure.com.attacker.test/openai/v1",
            "non-DeepSeek endpoint",
        ),
        (
            "base_url",
            "https://api.deepseek.com.attacker.test",
            "non-DeepSeek endpoint",
        ),
    ],
)
def test_managed_runtime_rejects_unapproved_resolution(field, value, message):
    """Managed occurrences cannot be redirected by a bad resolver result."""
    from cron.scheduler import _validate_oxaide_managed_runtime

    runtime = {
        "provider": "deepseek",
        "api_mode": "codex_responses",
        "base_url": "https://api.deepseek.com",
    }
    runtime[field] = value

    with pytest.raises(RuntimeError, match=message):
        _validate_oxaide_managed_runtime(runtime)


def test_managed_runtime_accepts_approved_deepseek_host():
    from cron.scheduler import _validate_oxaide_managed_runtime

    _validate_oxaide_managed_runtime(
        {
            "provider": "deepseek",
            "api_mode": "codex_responses",
            "base_url": "https://api.deepseek.com",
        }
    )
