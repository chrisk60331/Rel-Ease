"""Write bumped versions for Python, Node, and Rust projects."""

from __future__ import annotations

import json
import re
from pathlib import Path

import tomli_w

from rel_ease.repo import RepoContext, RepoKind
from rel_ease.semver_util import bump_part, parse_base_version


def bump_pyproject(path: Path, new_version: str) -> None:
    import tomllib

    raw = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    proj = data.setdefault("project", {})
    if "version" not in proj:
        raise ValueError("pyproject.toml [project] has no version field")
    proj["version"] = new_version
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def bump_package_json(path: Path, new_version: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "version" not in data:
        raise ValueError("package.json has no version field")
    data["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def bump_cargo_toml(path: Path, new_version: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_package = False
    replaced = False
    out: list[str] = []
    ver_re = re.compile(r'^(\s*version\s*=\s*)".*?"(\s*)$')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_package = stripped == "[package]"
            out.append(line)
            continue
        if in_package and ver_re.match(line) and not replaced:
            out.append(ver_re.sub(rf'\1"{new_version}"\2', line))
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise ValueError("Could not find [package] version in Cargo.toml")
    path.write_text("".join(out), encoding="utf-8")


def apply_bump(ctx: RepoContext, part_or_version: str, explicit_version: str | None) -> dict:
    """Bump by semver part (major|minor|patch) or set explicit_version."""
    if ctx.kind == RepoKind.UNKNOWN or not ctx.version_file:
        return {"ok": False, "error": "Unknown repo or no version file"}

    current = ctx.current_version
    if not current:
        return {"ok": False, "error": "Could not read current version"}

    if explicit_version:
        new_v = explicit_version.strip().lstrip("vV")
        if not parse_base_version(new_v):
            return {"ok": False, "error": f"Invalid semver: {explicit_version!r}"}
    else:
        try:
            new_v = bump_part(current, part_or_version)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    vf = ctx.version_file
    try:
        if ctx.kind == RepoKind.PYTHON:
            bump_pyproject(vf, new_v)
        elif ctx.kind == RepoKind.NODE:
            bump_package_json(vf, new_v)
        elif ctx.kind == RepoKind.RUST:
            bump_cargo_toml(vf, new_v)
        else:
            return {"ok": False, "error": f"Unsupported kind {ctx.kind}"}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "previous_version": current,
        "new_version": new_v,
        "file": str(vf.relative_to(ctx.root)),
        "repo_kind": ctx.kind.value,
    }


def npm_install_package_lock_only(root: Path) -> dict:
    import subprocess

    if not (root / "package.json").is_file():
        return {"ok": False, "error": "No package.json"}
    p = subprocess.run(
        ["npm", "install", "--package-lock-only"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "stdout": (p.stdout or "")[-4000:],
        "stderr": (p.stderr or "")[-4000:],
    }
