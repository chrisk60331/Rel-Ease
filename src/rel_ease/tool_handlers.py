"""Dispatch Backboard tool calls to local operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from rel_ease import git_ops, release_build, version_bump
from rel_ease.repo import RepoContext, RepoKind, detect_repo


def make_dispatcher(root: Path) -> Callable[[str, dict[str, Any]], str]:
    root = root.resolve()

    def dispatch(name: str, args: dict[str, Any]) -> str:
        try:
            out: dict[str, Any]
            if name == "get_repo_context":
                fresh = detect_repo(root)
                out = {
                    "repo_kind": fresh.kind.value,
                    "root": str(fresh.root),
                    "version_file": str(fresh.version_file) if fresh.version_file else None,
                    "current_version": fresh.current_version,
                    "package_name": fresh.package_name,
                    "release_notes_default": "release_notes.md",
                    "python_only_tools": ["uv_build", "twine_upload"],
                }
            elif name == "increment_version":
                part = args.get("semver_part") or "patch"
                explicit = args.get("explicit_version")
                fresh = detect_repo(root)
                out = version_bump.apply_bump(fresh, str(part), explicit)
                if out.get("ok") and fresh.kind == RepoKind.NODE:
                    lock = version_bump.npm_install_package_lock_only(root)
                    out["npm_lockfile"] = lock
            elif name == "git_diff":
                stat = bool(args.get("stat", True))
                paths = args.get("paths")
                plist = [str(p) for p in paths] if paths else None
                out = git_ops.git_diff(root, stat=stat, paths=plist)
            elif name == "git_status_files":
                out = git_ops.git_status_porcelain(root)
            elif name == "git_add":
                paths = args.get("paths", [])
                out = git_ops.git_add(root, [str(p) for p in paths])
            elif name == "git_commit":
                msg = args.get("message", "")
                if not msg:
                    out = {"ok": False, "error": "message required"}
                else:
                    out = git_ops.git_commit(root, str(msg))
            elif name == "uv_build":
                kind = detect_repo(root).kind
                if kind != RepoKind.PYTHON:
                    out = {
                        "skipped": True,
                        "reason": f"uv_build applies to python repos; this repo is {kind.value}",
                    }
                else:
                    out = release_build.uv_build(root)
            elif name == "release_notes_update":
                out = release_build.release_notes_write(
                    root,
                    str(args.get("content", "")),
                    str(args.get("path", "release_notes.md")),
                    str(args.get("mode", "replace")),
                )
            elif name == "twine_upload":
                kind = detect_repo(root).kind
                if kind != RepoKind.PYTHON:
                    out = {
                        "skipped": True,
                        "reason": f"twine_upload applies to python repos; this repo is {kind.value}",
                    }
                else:
                    url = args.get("repository_url")
                    out = release_build.twine_upload(
                        root, str(url) if url else None
                    )
            else:
                out = {"ok": False, "error": f"unknown tool {name}"}
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return json.dumps(out, indent=2)

    return dispatch


def initial_user_message(ctx: RepoContext, hint: str | None) -> str:
    base = (
        "Execute the full release sequence now — do not ask for confirmation at any step.\n\n"
        f"{ctx.summary()}\n"
    )
    if ctx.kind == RepoKind.PYTHON:
        base += "Python repo: after commit run uv_build then twine_upload.\n"
    elif ctx.kind == RepoKind.NODE:
        base += "Node/TypeScript repo: increment_version also updates package-lock.json.\n"
    elif ctx.kind == RepoKind.RUST:
        base += "Rust repo: bump Cargo.toml and commit; remind user to run `cargo publish` at the end.\n"
    else:
        base += "Unknown repo type: inspect files, pick the closest match, and proceed.\n"
    if hint:
        base += f"\nUser hint: {hint}\n"
    base += (
        "\nStart with git_status_files and git_diff, then execute every remaining step "
        "without pausing or asking questions."
    )
    return base
