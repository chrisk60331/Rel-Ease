"""Tiny SemVer 2.0 bump helpers (no external dependency)."""

from __future__ import annotations

import re

_SEMVER = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def parse_base_version(v: str) -> tuple[int, int, int] | None:
    v = v.strip().lstrip("vV")
    m = _SEMVER.match(v)
    if not m:
        return None
    return int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


def bump_part(version: str, part: str) -> str:
    """Return new X.Y.Z string; strips prerelease/build from bump target."""
    base = parse_base_version(version)
    if not base:
        raise ValueError(f"Not a simple semver base: {version!r}")
    major, minor, patch = base
    p = part.lower().strip()
    if p == "major":
        return f"{major + 1}.0.0"
    if p == "minor":
        return f"{major}.{minor + 1}.0"
    if p == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"part must be major|minor|patch, got {part!r}")
