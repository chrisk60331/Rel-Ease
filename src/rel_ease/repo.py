"""Detect project kind and current version from the repository root."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RepoKind(str, Enum):
    PYTHON = "python"
    NODE = "node_ts"
    RUST = "rust"
    UNKNOWN = "unknown"


@dataclass
class RepoContext:
    root: Path
    kind: RepoKind
    version_file: Path | None
    current_version: str | None
    package_name: str | None = None

    def summary(self) -> str:
        return (
            f"repo_kind={self.kind.value}\n"
            f"root={self.root.resolve()}\n"
            f"version_file={self.version_file}\n"
            f"current_version={self.current_version}\n"
            f"package_name={self.package_name}\n"
        )


def _read_cargo_version(root: Path) -> tuple[Path | None, str | None, str | None]:
    cargo = root / "Cargo.toml"
    if not cargo.is_file():
        return None, None, None
    text = cargo.read_text(encoding="utf-8")
    # First [package] block's version (simple heuristic)
    in_package = False
    name = None
    version = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_package = s == "[package]"
            continue
        if not in_package:
            continue
        m = re.match(r'^name\s*=\s*"([^"]+)"', s)
        if m:
            name = m.group(1)
        m = re.match(r'^version\s*=\s*"([^"]+)"', s)
        if m:
            version = m.group(1)
    if version:
        return cargo, version, name
    return cargo, None, name


def _read_pyproject_version(root: Path) -> tuple[Path | None, str | None, str | None]:
    py = root / "pyproject.toml"
    if not py.is_file():
        return None, None, None
    import tomllib

    data = tomllib.loads(py.read_text(encoding="utf-8"))
    proj = data.get("project") or {}
    ver = proj.get("version")
    name = proj.get("name")
    if isinstance(ver, str):
        return py, ver, name if isinstance(name, str) else None
    return py, None, name if isinstance(name, str) else None


def _read_package_json_version(root: Path) -> tuple[Path | None, str | None, str | None]:
    pj = root / "package.json"
    if not pj.is_file():
        return None, None, None
    data = json.loads(pj.read_text(encoding="utf-8"))
    ver = data.get("version")
    name = data.get("name")
    if isinstance(ver, str):
        return pj, ver, name if isinstance(name, str) else None
    return pj, None, None


def detect_repo(root: Path | None = None) -> RepoContext:
    """Pick python / node_ts / rust with deterministic precedence."""
    r = (root or Path.cwd()).resolve()
    if not r.is_dir():
        return RepoContext(r, RepoKind.UNKNOWN, None, None)

    cargo_path, cargo_ver, cargo_name = _read_cargo_version(r)
    py_path, py_ver, py_name = _read_pyproject_version(r)
    node_path, node_ver, node_name = _read_package_json_version(r)

    # Rust: explicit Cargo.toml with a package version wins when present.
    if cargo_path and cargo_ver:
        return RepoContext(r, RepoKind.RUST, cargo_path, cargo_ver, cargo_name)
    # Python: pyproject with a version in [project]
    if py_path and py_ver:
        return RepoContext(r, RepoKind.PYTHON, py_path, py_ver, py_name)
    # Node / TS
    if node_path and node_ver:
        return RepoContext(r, RepoKind.NODE, node_path, node_ver, node_name)
    # Partial matches (version missing)
    if py_path:
        return RepoContext(r, RepoKind.PYTHON, py_path, None, py_name)
    if node_path:
        return RepoContext(r, RepoKind.NODE, node_path, None, node_name)
    if cargo_path:
        return RepoContext(r, RepoKind.RUST, cargo_path, None, cargo_name)

    return RepoContext(r, RepoKind.UNKNOWN, None, None)
