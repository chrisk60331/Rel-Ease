"""Deterministic release API used by MCP and other callers."""

from __future__ import annotations

import os
import shutil
import subprocess
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeVar

import dotenv
from pydantic import BaseModel, Field, field_validator

from rel_ease import git_ops, release_build, version_bump
from rel_ease.assistant import describe_diff_with_llm
from rel_ease.repo import RepoContext, RepoKind, detect_repo
from rel_ease.semver_util import bump_part, parse_base_version

_JUNK_DIRS = {"dist", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".ruff_cache"}
_JUNK_EXTS = {".pyc", ".pyo", ".log"}
_SECRET_NAMES = {".env", ".env.local", ".env.production", "credentials.json"}
_T = TypeVar("_T")

dotenv.load_dotenv()


class DirectoryRequest(BaseModel):
    directory: str = Field(min_length=1, description="Project directory name under REL_EASE_PROJECTS_ROOT")

    @field_validator("directory")
    @classmethod
    def clean_directory(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("directory is required")
        return cleaned


class ReleaseRequest(DirectoryRequest):
    version: str = Field(min_length=5, description="New semantic version, for example 1.2.3")

    @field_validator("version")
    @classmethod
    def clean_version(cls, value: str) -> str:
        cleaned = value.strip().lstrip("vV")
        if not parse_base_version(cleaned):
            raise ValueError("version must be semantic version like 1.2.3")
        return cleaned


class ReleaseAPI:
    """Small, explicit API surface for release automation."""

    def __init__(self, projects_root: Path | None = None) -> None:
        default_root = Path(__file__).resolve().parents[3]
        configured = projects_root or Path(os.getenv("REL_EASE_PROJECTS_ROOT", str(default_root)))
        self.projects_root = configured.expanduser().resolve()

    def get_version(self, directory: str) -> dict[str, Any]:
        root = self._resolve_project(DirectoryRequest(directory=directory).directory)
        repo = self._detect_supported_repo(root)
        return self._repo_payload(repo)

    def get_incremental_version(self, directory: str) -> dict[str, Any]:
        root = self._resolve_project(DirectoryRequest(directory=directory).directory)
        repo = self._detect_supported_repo(root)
        current = self._require_current_version(repo)
        proposed = bump_part(current, "patch")
        return {**self._repo_payload(repo), "proposed_version": proposed, "increment": "patch"}

    def describe_diff(self, directory: str) -> dict[str, Any]:
        root = self._resolve_project(DirectoryRequest(directory=directory).directory)
        repo = self._detect_supported_repo(root)
        status = git_ops.git_status_porcelain(root)
        if status.get("exit_code") != 0:
            raise RuntimeError(status.get("stderr") or "git status failed")

        safe_files = [
            item for item in status.get("files", [])
            if not self._is_junk_or_secret(item["path"])
        ]
        skipped_files = [
            item["path"] for item in status.get("files", [])
            if self._is_junk_or_secret(item["path"])
        ]
        if not safe_files:
            return {
                "ok": True,
                **self._repo_payload(repo),
                "summary_md": "No releasable local changes.",
                "release_notes_md": "- No releasable local changes.",
                "commit_summary": "No releasable changes",
                "risk_notes": "None",
                "changed_files": [],
                "skipped_files": skipped_files,
            }

        api_key = os.environ.get("AI_LAYER_KEY") or os.environ.get("AI_LAYER_API_KEY")
        if not api_key:
            raise RuntimeError("AI_LAYER_KEY not set; describe_diff requires ai-layer")

        staged_diff = self._git_text(root, "diff", "--cached")
        unstaged_diff = self._git_text(root, "diff")
        description = self._run_async(
            lambda: describe_diff_with_llm(
                status_files=safe_files,
                repo_kind=repo.kind.value,
                current_version=repo.current_version,
                staged_diff=staged_diff,
                unstaged_diff=unstaged_diff,
                api_key=api_key,
            )
        )
        return {
            "ok": True,
            **self._repo_payload(repo),
            "summary_md": description.summary_md,
            "release_notes_md": description.release_notes_md,
            "commit_summary": description.commit_summary,
            "risk_notes": description.risk_notes,
            "changed_files": [item["path"] for item in safe_files],
            "skipped_files": skipped_files,
            "staged_diff_chars": len(staged_diff),
            "unstaged_diff_chars": len(unstaged_diff),
        }

    def list_recent_releases(self) -> list[dict[str, Any]]:
        releases: list[dict[str, Any]] = []
        for project in sorted(self.projects_root.iterdir(), key=lambda p: p.name.lower()):
            if not project.is_dir() or not (project / ".git").is_dir():
                continue
            repo = detect_repo(project)
            latest = self._latest_tag(project)
            if latest:
                releases.append({
                    "project": project.name,
                    "directory": str(project),
                    "repo_kind": repo.kind.value,
                    "current_version": repo.current_version,
                    **latest,
                })
        releases.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return releases[:25]

    def release(self, directory: str, version: str) -> dict[str, Any]:
        request = ReleaseRequest(directory=directory, version=version)
        root = self._resolve_project(request.directory)
        repo = self._detect_supported_repo(root)
        current = self._require_current_version(repo)
        self._require_increasing_version(current, request.version)

        status_before = git_ops.git_status_porcelain(root)
        if status_before.get("exit_code") != 0:
            raise RuntimeError(status_before.get("stderr") or "git status failed")

        diff_description = self.describe_diff(request.directory)
        notes_body = self._release_notes(repo, request.version, diff_description)
        bump_result = version_bump.apply_bump(repo, "patch", request.version)
        if not bump_result.get("ok"):
            raise RuntimeError(f"version bump failed: {bump_result.get('error')}")

        npm_lock: dict[str, Any] | None = None
        if repo.kind == RepoKind.NODE:
            npm_lock = version_bump.npm_install_package_lock_only(root)
            if not npm_lock.get("ok"):
                raise RuntimeError(f"npm lock refresh failed: {npm_lock.get('stderr') or npm_lock.get('error')}")

        notes_result = release_build.release_notes_write(
            root,
            notes_body,
            mode="append" if (root / "release_notes.md").is_file() else "replace",
        )
        if not notes_result.get("ok"):
            raise RuntimeError(f"release notes failed: {notes_result.get('error')}")

        build_result: dict[str, Any] | None = None
        if repo.kind == RepoKind.PYTHON:
            build_result = release_build.uv_build(root)
            if not build_result.get("ok"):
                raise RuntimeError(f"uv build failed: {build_result.get('stderr')}")

        status_after = git_ops.git_status_porcelain(root)
        stage_paths, skipped_paths = self._stageable_paths(status_after.get("files", []))
        if not stage_paths:
            raise RuntimeError("nothing to stage")
        add_result = git_ops.git_add(root, stage_paths)
        if not add_result.get("ok"):
            raise RuntimeError(f"git add failed: {add_result.get('stderr')}")

        tag = f"v{request.version}"
        commit_message = f"Release {tag}"
        commit_result = git_ops.git_commit(root, commit_message, no_gpg_sign=True)
        if not commit_result.get("ok"):
            raise RuntimeError(f"git commit failed: {commit_result.get('stderr')}")

        tag_result = git_ops.git_tag(root, tag, message=commit_message, no_gpg_sign=True)
        if not tag_result.get("ok"):
            raise RuntimeError(f"git tag failed: {tag_result.get('stderr')}")

        push_result = git_ops.git_push(root, follow_tags=True)
        if not push_result.get("ok"):
            raise RuntimeError(f"git push failed: {push_result.get('stderr')}")

        github_release = git_ops.gh_release_create(root, tag, commit_message, notes_body)
        if not github_release.get("ok"):
            raise RuntimeError(
                f"GitHub release failed: {github_release.get('stderr') or github_release.get('error')}"
            )

        upload_result: dict[str, Any] | None = None
        if repo.kind == RepoKind.PYTHON:
            upload_result = release_build.twine_upload(root)
            if not upload_result.get("ok"):
                raise RuntimeError(f"twine upload failed: {upload_result.get('stderr') or upload_result.get('error')}")

        return {
            "ok": True,
            "project": root.name,
            "directory": str(root),
            "previous_version": current,
            "new_version": request.version,
            "tag": tag,
            "commit_message": commit_message,
            "staged_paths": stage_paths,
            "skipped_paths": skipped_paths,
            "diff_description": diff_description,
            "release_notes": notes_result,
            "npm_lock": npm_lock,
            "build": build_result,
            "github_release": github_release,
            "upload": upload_result,
        }

    def _resolve_project(self, directory: str) -> Path:
        raw = Path(directory).expanduser()
        candidate = raw.resolve() if raw.is_absolute() else (self.projects_root / raw).resolve()
        if candidate != self.projects_root and self.projects_root not in candidate.parents:
            raise ValueError(f"directory must be inside {self.projects_root}")
        if not candidate.is_dir():
            raise ValueError(f"project directory not found: {directory}")
        if not (candidate / ".git").is_dir():
            raise ValueError(f"not a git repository: {candidate}")
        return candidate

    def _detect_supported_repo(self, root: Path) -> RepoContext:
        repo = detect_repo(root)
        if repo.kind == RepoKind.UNKNOWN:
            raise ValueError(f"unsupported project type: {root}")
        if not repo.version_file:
            raise ValueError(f"no version file found in {root}")
        return repo

    def _require_current_version(self, repo: RepoContext) -> str:
        if not repo.current_version or not parse_base_version(repo.current_version):
            raise ValueError(f"current version is not valid semver: {repo.current_version}")
        return repo.current_version

    def _require_increasing_version(self, current: str, new_version: str) -> None:
        current_base = parse_base_version(current)
        new_base = parse_base_version(new_version)
        if not current_base or not new_base:
            raise ValueError("current and new versions must be valid semver")
        if new_base <= current_base:
            raise ValueError(f"new version must be greater than current version {current}")

    def _repo_payload(self, repo: RepoContext) -> dict[str, Any]:
        return {
            "project": repo.root.name,
            "directory": str(repo.root),
            "repo_kind": repo.kind.value,
            "version_file": str(repo.version_file.relative_to(repo.root)) if repo.version_file else None,
            "current_version": repo.current_version,
            "package_name": repo.package_name,
        }

    def _release_notes(self, repo: RepoContext, version: str, diff_description: dict[str, Any]) -> str:
        today = date.today().strftime("%Y-%m-%d")
        package = repo.package_name or repo.root.name
        body = str(diff_description.get("release_notes_md") or "").strip()
        lines = [
            f"## [{version}] - {today}",
            "",
            body or f"- Release {package} {version}.",
        ]
        return "\n".join(lines)

    def _stageable_paths(self, status_files: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        paths: list[str] = []
        skipped: list[str] = []
        for item in status_files:
            path = item["path"]
            if self._is_junk_or_secret(path):
                skipped.append(path)
                continue
            paths.append(path)
        return sorted(set(paths)), sorted(set(skipped))

    def _is_junk_or_secret(self, path_str: str) -> bool:
        path = Path(path_str)
        if path.name in _SECRET_NAMES or path.suffix in _JUNK_EXTS:
            return True
        return any(part in _JUNK_DIRS for part in path.parts)

    def _git_text(self, root: Path, *args: str) -> str:
        process = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if process.returncode != 0:
            raise RuntimeError(process.stderr or f"git {' '.join(args)} failed")
        return process.stdout or ""

    def _run_async(self, factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(factory())
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(factory())).result()

    def _latest_tag(self, root: Path) -> dict[str, str] | None:
        if not shutil.which("git"):
            return None
        process = subprocess.run(
            ["git", "for-each-ref", "--sort=-creatordate", "--format=%(creatordate:iso8601)%09%(refname:short)", "refs/tags"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if process.returncode != 0:
            return None
        first = next((line for line in process.stdout.splitlines() if line.strip()), None)
        if not first:
            return None
        created_at, tag = first.split("\t", 1)
        return {"tag": tag, "created_at": created_at}
