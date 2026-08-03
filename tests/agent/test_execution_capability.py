from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.execution_capability import ExecutionCapability, ExecutionRevokedError


def test_capability_starts_active_and_revokes_monotonically():
    capability = ExecutionCapability()

    capability.require_active("model request")
    assert capability.is_active
    assert capability.revoke("lease lost") is True
    assert capability.revoke("later reason") is False
    assert not capability.is_active
    assert capability.revoke_reason == "lease lost"

    with pytest.raises(ExecutionRevokedError, match="model request.*lease lost"):
        capability.require_active("model request")


def test_concurrent_revocation_has_exactly_one_winner():
    capability = ExecutionCapability()

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(capability.revoke, [f"reason-{i}" for i in range(64)]))

    assert results.count(True) == 1
    assert results.count(False) == 63
    assert not capability.is_active
    assert capability.revoke_reason is not None