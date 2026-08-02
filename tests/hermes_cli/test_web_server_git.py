import subprocess
from pathlib import Path

import pytest

from hermes_cli import web_server

pytest.importorskip("starlette.testclient")
from starlette.testclient import TestClient


@pytest.fixture
def client():
    previous = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    test_client = TestClient(web_server.app)
    test_client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        yield test_client
    finally:
        if previous is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _git_text(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\ntwo\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    # A tracked modification + a brand-new untracked file (the new-file case the
    # rail/review must surface).
    (root / "a.txt").write_text("one\ntwo\nthree\n")
    (root / "new.py").write_text("print(1)\nprint(2)\n")
    return root


@pytest.fixture
def hosted_repo(client, monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\ntwo\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")

    (root / "a.txt").write_text("one\ntwo\nthree\n")
    (root / "new.py").write_text("print('safe')\n")
    results = root / "research-results"
    results.mkdir()
    artifact = results / "private.md"
    artifact.write_text("TOP SECRET RESULT\n")
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(root))
    return root, artifact


def test_status_reports_branch_and_change_counts(client, repo):
    body = client.get("/api/git/status", params={"path": str(repo)}).json()

    assert body["branch"] == body["defaultBranch"]
    assert body["branch"]
    assert body["detached"] is False
    # 1 tracked-modified + 1 untracked = 2 changed paths.
    assert body["changed"] == 2
    assert body["untracked"] == 1
    # +1 (a.txt) folded with +2 (untracked new.py) since `git diff HEAD` skips untracked.
    assert body["added"] == 3
    assert {f["path"] for f in body["files"]} == {"a.txt", "new.py"}


def test_status_returns_null_outside_repo(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    assert client.get("/api/git/status", params={"path": str(plain)}).json() is None


def test_review_list_classifies_modified_and_untracked(client, repo):
    body = client.get("/api/git/review/list", params={"path": str(repo)}).json()

    files = {f["path"]: f for f in body["files"]}
    assert files["a.txt"]["status"] == "M"
    assert files["a.txt"]["added"] == 1
    assert files["new.py"]["status"] == "?"
    assert files["new.py"]["added"] == 2  # untracked insertions counted from disk


def test_review_diff_shows_change_and_synthesizes_untracked(client, repo):
    tracked = client.get(
        "/api/git/review/diff", params={"path": str(repo), "file": "a.txt"}
    ).json()["diff"]
    assert "+three" in tracked

    untracked = client.get(
        "/api/git/review/diff", params={"path": str(repo), "file": "new.py"}
    ).json()["diff"]
    assert "print(1)" in untracked  # all-add diff for a file git doesn't track yet


def test_stage_commit_roundtrip_clears_changes(client, repo):
    assert client.post("/api/git/review/stage", json={"path": str(repo), "file": "a.txt"}).json() == {"ok": True}
    staged = client.get("/api/git/status", params={"path": str(repo)}).json()
    assert staged["staged"] >= 1

    assert client.post(
        "/api/git/review/commit", json={"path": str(repo), "message": "tracked change", "push": False}
    ).json() == {"ok": True}

    after = client.get("/api/git/status", params={"path": str(repo)}).json()
    # The tracked change is committed; only the untracked file remains.
    assert after["changed"] == 1
    assert after["untracked"] == 1


def test_commit_with_nothing_staged_commits_all_changes(client, repo):
    assert client.post(
        "/api/git/review/commit", json={"path": str(repo), "message": "commit all", "push": False}
    ).json() == {"ok": True}

    assert client.get("/api/git/status", params={"path": str(repo)}).json()["changed"] == 0


def test_worktrees_and_branch_lifecycle(client, repo):
    worktrees = client.get("/api/git/worktrees", params={"path": str(repo)}).json()["worktrees"]
    assert any(tree["isMain"] and tree["path"] == str(repo) for tree in worktrees)

    added = client.post(
        "/api/git/worktree/add", json={"path": str(repo), "branch": "feature/x"}
    ).json()
    assert added["branch"] == "feature/x"
    assert Path(added["path"]).is_dir()

    branches = client.get("/api/git/branches", params={"path": str(repo)}).json()["branches"]
    assert any(b["name"] == "feature/x" and b["checkedOut"] for b in branches)

    removed = client.post(
        "/api/git/worktree/remove", json={"path": str(repo), "worktreePath": added["path"], "force": True}
    ).json()
    assert removed["removed"]


def test_worktree_add_initializes_plain_folder(client, tmp_path):
    folder = tmp_path / "plain-project"
    folder.mkdir()
    (folder / "notes.txt").write_text("not committed\n")

    added = client.post(
        "/api/git/worktree/add", json={"path": str(folder), "branch": "feature/plain"}
    ).json()

    assert added["branch"] == "feature/plain"
    assert Path(added["path"]).is_dir()
    assert (folder / ".git").exists()
    _git(folder, "rev-parse", "--verify", "HEAD")

    status = client.get("/api/git/status", params={"path": str(folder)}).json()
    assert status["branch"] == status["defaultBranch"]
    assert status["branch"]
    # Existing files are not silently committed by repo initialization.
    assert any(file["path"] == "notes.txt" and file["untracked"] for file in status["files"])


def test_commit_context_includes_diff_and_untracked(client, repo):
    body = client.get("/api/git/review/commit-context", params={"path": str(repo)}).json()

    assert "+three" in body["diff"]
    assert "new.py" in body["diff"]  # untracked files listed since they carry no diff


def test_ship_info_degrades_without_gh(client, repo, monkeypatch):
    monkeypatch.setattr(web_server._web_git.shutil, "which", lambda _name: None)

    assert client.get("/api/git/review/ship-info", params={"path": str(repo)}).json() == {
        "ghReady": False,
        "pr": None,
    }


def test_git_endpoints_require_auth(repo):
    unauth = TestClient(web_server.app)

    assert unauth.get("/api/git/status", params={"path": str(repo)}).status_code == 401
    assert unauth.post("/api/git/review/stage", json={"path": str(repo)}).status_code == 401


def test_hosted_status_review_and_context_hide_results(client, hosted_repo):
    root, artifact = hosted_repo
    _git(root, "add", "--", "research-results/private.md")
    _git(root, "commit", "-qm", "historical private artifact")
    artifact.write_text("UPDATED TOP SECRET RESULT\n")

    status = client.get("/api/git/status", params={"path": str(root)})
    review = client.get("/api/git/review/list", params={"path": str(root)})
    context = client.get(
        "/api/git/review/commit-context",
        params={"path": str(root)},
    )

    assert status.status_code == 200
    assert {item["path"] for item in status.json()["files"]} == {"a.txt", "new.py"}
    assert review.status_code == 200
    assert {item["path"] for item in review.json()["files"]} == {"a.txt", "new.py"}
    assert context.status_code == 200
    assert "+three" in context.json()["diff"]
    assert "new.py" in context.json()["diff"]
    assert context.json()["recent"] == ""
    for response in (status, review, context):
        assert artifact.name not in response.text
        assert "research-results" not in response.text
        assert "TOP SECRET RESULT" not in response.text


def test_hosted_git_rejects_result_paths_aliases_and_traversal(client, hosted_repo, tmp_path):
    root, artifact = hosted_repo
    alias = root / "ordinary.md"
    try:
        alias.symlink_to(artifact)
    except OSError:
        pytest.skip("filesystem does not allow file symlinks")
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret")

    responses = (
        client.get(
            "/api/git/review/diff",
            params={"path": str(root), "file": "research-results/private.md"},
        ),
        client.get(
            "/api/git/review/diff",
            params={"path": str(root), "file": "ordinary.md"},
        ),
        client.get(
            "/api/git/file-diff",
            params={"path": str(root), "file": "../outside.md"},
        ),
        client.post(
            "/api/git/review/stage",
            json={"path": str(root), "file": "research-results/private.md"},
        ),
        client.get(
            "/api/git/review/rev-parse",
            params={
                "path": str(root),
                "ref": "HEAD:research-results/private.md",
            },
        ),
    )

    assert {response.status_code for response in responses} == {400}
    for response in responses:
        assert "TOP SECRET RESULT" not in response.text
        assert str(root) not in response.text


def test_hosted_git_reservation_is_top_level_and_exact(client, hosted_repo):
    root, _artifact = hosted_repo
    similarly_named = root / "research-results-copy" / "report.md"
    nested = root / "projects" / "research-results" / "report.md"
    for path in (similarly_named, nested):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ordinary file\n")

    response = client.get("/api/git/status", params={"path": str(root)})

    assert response.status_code == 200
    paths = {item["path"] for item in response.json()["files"]}
    assert "research-results-copy/" in paths
    assert "projects/" in paths
    for file_path in (
        "research-results-copy/report.md",
        "projects/research-results/report.md",
    ):
        diff = client.get(
            "/api/git/review/diff",
            params={"path": str(root), "file": file_path},
        )
        assert diff.status_code == 200
        assert "+ordinary file" in diff.json()["diff"]


def test_hosted_broad_stage_commit_and_revert_preserve_results(client, hosted_repo):
    root, artifact = hosted_repo
    _git(root, "add", "--", "research-results/private.md")
    _git(root, "commit", "-qm", "historical private artifact")

    staged = client.post(
        "/api/git/review/stage",
        json={"path": str(root), "file": None},
    )
    assert staged.status_code == 200
    staged_paths = set(_git_text(root, "diff", "--cached", "--name-only").splitlines())
    assert staged_paths == {"a.txt", "new.py"}

    committed = client.post(
        "/api/git/review/commit",
        json={"path": str(root), "message": "safe changes", "push": False},
    )
    assert committed.status_code == 200
    assert artifact.read_text() == "TOP SECRET RESULT\n"
    assert "research-results/private.md" in _git_text(root, "ls-files").splitlines()

    artifact.write_text("UPDATED TOP SECRET RESULT\n")
    disposable = root / "disposable.txt"
    disposable.write_text("remove me")
    reverted = client.post(
        "/api/git/review/revert",
        json={"path": str(root), "file": None},
    )
    assert reverted.status_code == 200
    assert not disposable.exists()
    assert artifact.read_text() == "UPDATED TOP SECRET RESULT\n"


def test_hosted_commit_rejects_pre_staged_result(client, hosted_repo):
    root, artifact = hosted_repo
    _git(root, "add", "--", "research-results/private.md")
    before = _git_text(root, "rev-parse", "HEAD").strip()

    status = client.get("/api/git/status", params={"path": str(root)})
    committed = client.post(
        "/api/git/review/commit",
        json={"path": str(root), "message": "must not commit", "push": False},
    )

    assert status.status_code == 200
    assert artifact.name not in status.text
    assert committed.status_code == 400
    assert "TOP SECRET RESULT" not in committed.text
    assert _git_text(root, "rev-parse", "HEAD").strip() == before
    assert "research-results/private.md" in _git_text(
        root,
        "diff",
        "--cached",
        "--name-only",
    ).splitlines()


def test_hosted_disables_external_diff_and_commit_hooks(client, hosted_repo, monkeypatch):
    root, _artifact = hosted_repo
    marker = root / ".git" / "executed"
    executable = root / ".git" / "unexpected-command.sh"
    executable.write_text(f"#!/bin/sh\nprintf ran > {marker}\n")
    executable.chmod(0o700)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(executable.read_text())
    hook.chmod(0o700)
    _git(root, "config", "diff.external", str(executable))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(executable))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.inherited.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(executable))
    (root / ".gitattributes").write_text("a.txt filter=inherited\n")

    diff = client.get(
        "/api/git/review/diff",
        params={"path": str(root), "file": "a.txt"},
    )
    staged = client.post(
        "/api/git/review/stage",
        json={"path": str(root), "file": "a.txt"},
    )
    committed = client.post(
        "/api/git/review/commit",
        json={"path": str(root), "message": "hooks disabled", "push": False},
    )

    assert diff.status_code == 200
    assert "+three" in diff.json()["diff"]
    assert staged.status_code == 200
    assert committed.status_code == 200
    assert not marker.exists()


@pytest.mark.parametrize("metadata_kind", ["include", "alternates"])
def test_hosted_rejects_git_metadata_that_can_read_other_files(
    client,
    hosted_repo,
    metadata_kind,
):
    root, artifact = hosted_repo
    if metadata_kind == "include":
        artifact.write_text("[core]\n\tbare = false\n")
        with (root / ".git" / "config").open("a") as config:
            config.write(f"\n[include]\n\tpath = {artifact}\n")
    else:
        alternates = root / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(f"{artifact}\n")

    response = client.get("/api/git/status", params={"path": str(root)})

    assert response.status_code == 200
    assert response.json() is None
    assert "TOP SECRET RESULT" not in response.text


def test_hosted_rejects_executable_clean_filters(client, hosted_repo):
    root, _artifact = hosted_repo
    marker = root / ".git" / "filter-executed"
    executable = root / ".git" / "filter.sh"
    executable.write_text(f"#!/bin/sh\nprintf ran > {marker}\ncat\n")
    executable.chmod(0o700)
    _git(root, "config", "filter.evil.clean", str(executable))
    (root / ".gitattributes").write_text("a.txt filter=evil\n")

    response = client.post(
        "/api/git/review/stage",
        json={"path": str(root), "file": "a.txt"},
    )

    assert response.status_code == 400
    assert not marker.exists()


def test_hosted_blocks_outbound_branch_and_worktree_mutations(client, hosted_repo):
    root, _artifact = hosted_repo
    before = _git_text(root, "rev-parse", "HEAD").strip()

    blocked = (
        client.post("/api/git/review/push", json={"path": str(root)}),
        client.post("/api/git/review/create-pr", json={"path": str(root)}),
        client.post(
            "/api/git/review/commit",
            json={"path": str(root), "message": "no push", "push": True},
        ),
        client.post(
            "/api/git/branch/switch",
            json={"path": str(root), "branch": "feature/x"},
        ),
        client.post(
            "/api/git/worktree/add",
            json={"path": str(root), "branch": "feature/x"},
        ),
        client.post(
            "/api/git/worktree/remove",
            json={
                "path": str(root),
                "worktreePath": str(root / ".worktrees" / "feature-x"),
                "force": True,
            },
        ),
    )

    assert {response.status_code for response in blocked} == {400}
    assert _git_text(root, "rev-parse", "HEAD").strip() == before
    ship = client.get("/api/git/review/ship-info", params={"path": str(root)})
    assert ship.status_code == 200
    assert ship.json() == {"ghReady": False, "pr": None}


def test_hosted_gitdir_pointer_is_rejected_before_git_runs(
    client,
    monkeypatch,
    tmp_path,
):
    root = tmp_path / "data"
    project = root / "project"
    outside = tmp_path / "outside"
    project.mkdir(parents=True)
    outside.mkdir()
    _git(outside, "init", "-q")
    (project / ".git").write_text(f"gitdir: {outside / '.git'}\n")
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(root))
    original_run = subprocess.run
    calls = []

    def recording_run(*args, **kwargs):
        calls.append(args[0])
        return original_run(*args, **kwargs)

    monkeypatch.setattr(web_server._web_git.subprocess, "run", recording_run)

    response = client.get("/api/git/status", params={"path": str(project)})

    assert response.status_code == 200
    assert response.json() is None
    assert calls == []
