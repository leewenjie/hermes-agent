"""Shared, monotonically revocable permission to start managed work."""

from __future__ import annotations

import threading
from typing import Optional


class ExecutionRevokedError(RuntimeError):
    """Raised when managed work is attempted after its lease was revoked."""


class ExecutionCapability:
    """Thread-safe permission shared by every worker in one managed turn.

    Revocation is monotonic: a capability cannot be reset or reused for a
    later turn. Managed runtimes create a fresh instance after authorization
    and pass that exact object to all descendants and detached workers.
    """

    def __init__(self) -> None:
        self._revoked = threading.Event()
        self._revoke_lock = threading.Lock()
        self._revoke_reason: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return not self._revoked.is_set()

    @property
    def revoke_reason(self) -> Optional[str]:
        with self._revoke_lock:
            return self._revoke_reason

    def revoke(self, reason: Optional[str] = None) -> bool:
        """Revoke this capability, returning True only for the first call."""
        with self._revoke_lock:
            if self._revoked.is_set():
                return False
            self._revoke_reason = reason
            self._revoked.set()
            return True

    def require_active(self, operation: str = "managed execution") -> None:
        """Raise when *operation* may no longer start."""
        if not self._revoked.is_set():
            return
        reason = self.revoke_reason
        detail = f": {reason}" if reason else ""
        raise ExecutionRevokedError(f"Cannot start {operation}; capability revoked{detail}")