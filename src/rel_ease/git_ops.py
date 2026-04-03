"""Git subprocess helpers — structured output for the assistant."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(cwd: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def git_diff(cwd: Path, stat: bool = True, paths: list[str] | None = None) -> dict:
    cmd = ["diff"]
    if stat:
        cmd.append("--stat")
    if paths:
        cmd.extend(["--", *paths])
    p = _run_git(cwd, *cmd)
    return {
        "exit_code": p.returncode,
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
    }


def git_status_porcelain(cwd: Path) -> dict:
    p = _run_git(cwd, "status", "--porcelain=v1", "-u")
    lines = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
    files: list[dict] = []
    for ln in lines:
        if len(ln) < 4:
            continue
        idx = ln[:2]
        work = ln[3:].strip()
        # Handle renamed " -> "
        path = work.split(" -> ")[-1].strip()
        files.append({"index_worktree": idx, "path": path, "raw": ln})
    return {
        "exit_code": p.returncode,
        "files": files,
        "stderr": p.stderr or "",
    }


def git_add(cwd: Path, paths: list[str]) -> dict:
    if not paths:
        return {"ok": False, "error": "paths required"}
    p = _run_git(cwd, "add", "--", *paths)
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
    }


def git_commit(cwd: Path, message: str) -> dict:
    p = _run_git(cwd, "commit", "-m", message)
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
    }


def git_tag(cwd: Path, tag: str, message: str | None = None) -> dict:
    if message:
        p = _run_git(cwd, "tag", "-a", tag, "-m", message)
    else:
        p = _run_git(cwd, "tag", tag)
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "stderr": p.stderr or "",
    }


def git_current_branch(cwd: Path) -> str:
    p = _run_git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return (p.stdout or "main").strip()


def git_push(cwd: Path, follow_tags: bool = True) -> dict:
    args = ["push"]
    if follow_tags:
        args.append("--follow-tags")
    p = _run_git(cwd, *args, timeout=180)
    if p.returncode != 0 and "no upstream branch" in (p.stderr or ""):
        branch = git_current_branch(cwd)
        fallback_args = ["push", "--set-upstream", "origin", branch]
        if follow_tags:
            fallback_args.append("--follow-tags")
        p = _run_git(cwd, *fallback_args, timeout=180)
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
    }
