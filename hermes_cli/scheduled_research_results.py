"""Shared filesystem policy for hosted scheduled-research results.

Hosted occurrence artifacts live in one reserved top-level directory beneath
Hermes' locked dashboard root. Generic Files, legacy FS, Git, and media routes
must never enter that namespace; the occurrence-authorized result route is the
only hosted reader.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
from typing import Iterator
import uuid

from hermes_constants import get_default_hermes_root, get_hermes_home


MANAGED_FILES_ROOT_ENV = "HERMES_DASHBOARD_FILES_ROOT"
HOSTED_MANAGED_FILES_ROOT = Path("/opt/data")
RESULTS_DIRECTORY_NAME = "research-results"
RESULT_MAX_BYTES = 2 * 1024 * 1024


class ReservedScheduledResearchPath(ValueError):
    """Raised when a generic hosted filesystem path reaches result storage."""


class ScheduledResearchResultTooLarge(ValueError):
    """Raised before persistence when a result exceeds the fixed byte limit."""


class ScheduledResearchResultUnavailable(OSError):
    """Raised when a stored result fails the private artifact contract."""


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def default_hermes_root_is_hosted() -> bool:
    """Return whether the canonical Hermes root is the hosted ``/opt/data``."""
    if not os.environ.get("HERMES_HOME", "").strip():
        return False
    try:
        return _canonical(get_default_hermes_root()) == HOSTED_MANAGED_FILES_ROOT
    except (OSError, RuntimeError):
        return False


def hosted_files_root() -> Path | None:
    """Return the locked hosted file root, without creating it.

    A remote auth gate alone does not make a local dashboard hosted. The fence
    activates only for the explicit managed-files root or the canonical
    ``HERMES_HOME=/opt/data`` container layout.
    """
    configured = os.environ.get(MANAGED_FILES_ROOT_ENV, "").strip()
    if configured:
        return _canonical(Path(configured))
    if default_hermes_root_is_hosted():
        return HOSTED_MANAGED_FILES_ROOT
    return None


def scheduled_research_storage_root() -> Path:
    """Return the parent under which the reserved result directory lives.

    Hosted execution shares the exact root used by Files/FS/Git policy. Local
    development keeps its historical Hermes-home storage behavior without
    activating the hosted reservation.
    """
    return hosted_files_root() or _canonical(get_hermes_home())


def results_directory(root: Path | None = None) -> Path:
    return (root or scheduled_research_storage_root()) / RESULTS_DIRECTORY_NAME


def result_filename(artifact_id: str) -> str:
    return f"{uuid.UUID(str(artifact_id))}.md"


def result_path(artifact_id: str) -> Path:
    """Return the derived path for display/tests, never for opening a result."""
    return results_directory() / result_filename(artifact_id)


def _directory_open_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ScheduledResearchResultUnavailable(
            "secure scheduled-research directory access is unavailable"
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def _open_results_directory(*, create: bool) -> Iterator[int]:
    """Open the storage root and result directory without following either leaf."""
    root = scheduled_research_storage_root()
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)

    root_fd: int | None = None
    result_fd: int | None = None
    try:
        root_fd = os.open(root, _directory_open_flags())
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise ScheduledResearchResultUnavailable(
                "scheduled-research storage root is not a directory"
            )
        if create:
            try:
                os.mkdir(RESULTS_DIRECTORY_NAME, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        result_fd = os.open(
            RESULTS_DIRECTORY_NAME,
            _directory_open_flags(),
            dir_fd=root_fd,
        )
        if not stat.S_ISDIR(os.fstat(result_fd).st_mode):
            raise ScheduledResearchResultUnavailable(
                "scheduled-research result storage is not a directory"
            )
        if create:
            os.fchmod(result_fd, 0o700)
        yield result_fd
    finally:
        if result_fd is not None:
            os.close(result_fd)
        if root_fd is not None:
            os.close(root_fd)


def encode_result_text(content: str) -> bytes:
    encoded = content.encode("utf-8", errors="strict")
    if len(encoded) > RESULT_MAX_BYTES:
        raise ScheduledResearchResultTooLarge(
            f"scheduled research result exceeds {RESULT_MAX_BYTES} bytes"
        )
    return encoded


def write_result_text(artifact_id: str, content: str) -> str:
    """Publish one immutable result and return only its opaque artifact UUID."""
    normalized_artifact_id = str(uuid.UUID(str(artifact_id)))
    filename = result_filename(normalized_artifact_id)
    encoded = encode_result_text(content)
    temporary = f".{filename}.{uuid.uuid4().hex}.tmp"
    temp_fd: int | None = None
    try:
        with _open_results_directory(create=True) as result_fd:
            published = False
            try:
                temp_fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=result_fd,
                )
                view = memoryview(encoded)
                while view:
                    written = os.write(temp_fd, view)
                    if written <= 0:
                        raise OSError(
                            "scheduled-research result write made no progress"
                        )
                    view = view[written:]
                os.fchmod(temp_fd, 0o600)
                os.fsync(temp_fd)
                os.close(temp_fd)
                temp_fd = None
                os.link(
                    temporary,
                    filename,
                    src_dir_fd=result_fd,
                    dst_dir_fd=result_fd,
                    follow_symlinks=False,
                )
                published = True
                os.unlink(temporary, dir_fd=result_fd)
                os.fsync(result_fd)
            except BaseException:
                if published:
                    try:
                        os.unlink(filename, dir_fd=result_fd)
                    except OSError:
                        pass
                raise
            finally:
                if temp_fd is not None:
                    os.close(temp_fd)
                try:
                    os.unlink(temporary, dir_fd=result_fd)
                except FileNotFoundError:
                    pass
    except ScheduledResearchResultUnavailable:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ScheduledResearchResultUnavailable(
            "scheduled-research result storage is unavailable"
        ) from exc
    return normalized_artifact_id


def delete_result(artifact_id: str) -> bool:
    """Best-effort removal for an artifact whose lease never committed."""
    filename = result_filename(artifact_id)
    try:
        with _open_results_directory(create=False) as result_fd:
            os.unlink(filename, dir_fd=result_fd)
            os.fsync(result_fd)
            return True
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError, ValueError):
        return False


def read_result_text(artifact_id: str) -> str:
    """Read one regular result with a no-follow leaf and a hard byte ceiling."""
    filename = result_filename(artifact_id)
    file_fd: int | None = None
    try:
        with _open_results_directory(create=False) as result_fd:
            entry = os.stat(filename, dir_fd=result_fd, follow_symlinks=False)
            if not stat.S_ISREG(entry.st_mode) or entry.st_size > RESULT_MAX_BYTES:
                raise ScheduledResearchResultUnavailable(
                    "scheduled-research result is unavailable"
                )
            file_fd = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=result_fd,
            )
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > RESULT_MAX_BYTES:
                raise ScheduledResearchResultUnavailable(
                    "scheduled-research result is unavailable"
                )
            chunks: list[bytes] = []
            remaining = RESULT_MAX_BYTES + 1
            while remaining > 0:
                chunk = os.read(file_fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            encoded = b"".join(chunks)
            if len(encoded) > RESULT_MAX_BYTES:
                raise ScheduledResearchResultUnavailable(
                    "scheduled-research result is unavailable"
                )
            try:
                return encoded.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ScheduledResearchResultUnavailable(
                    "scheduled-research result is unavailable"
                ) from exc
    except ScheduledResearchResultUnavailable:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ScheduledResearchResultUnavailable(
            "scheduled-research result is unavailable"
        ) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _is_under(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _has_reserved_first_component(root: Path, target: Path) -> bool:
    if not _is_under(root, target):
        return False
    relative = target.relative_to(root)
    return bool(
        relative.parts
        and os.path.normcase(relative.parts[0])
        == os.path.normcase(RESULTS_DIRECTORY_NAME)
    )


def is_reserved_result_path(
    root: Path,
    lexical_path: Path,
    *,
    canonical_path: Path | None = None,
) -> bool:
    """Check both lexical and canonical membership in the reserved namespace.

    The lexical check rejects a ``research-results`` symlink that points out of
    the root. The canonical check rejects an ordinary-looking alias that points
    into the result directory. Callers should pass their already-resolved path
    when available to avoid resolving it twice.
    """
    canonical_root = _canonical(root)
    lexical_absolute = Path(
        os.path.abspath(os.fspath(lexical_path.expanduser()))
    )
    if _has_reserved_first_component(canonical_root, lexical_absolute):
        return True
    try:
        resolved = canonical_path or _canonical(lexical_path)
    except (OSError, RuntimeError):
        return False
    return _has_reserved_first_component(canonical_root, resolved)


def reject_reserved_result_path(
    root: Path | None,
    lexical_path: Path,
    *,
    canonical_path: Path | None = None,
) -> None:
    if root is not None and is_reserved_result_path(
        root,
        lexical_path,
        canonical_path=canonical_path,
    ):
        raise ReservedScheduledResearchPath("scheduled research results are reserved")
