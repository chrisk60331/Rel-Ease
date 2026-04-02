"""Build and publish steps (uv / twine) for Python packages."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def uv_build(root: Path) -> dict:
    dist = root / "dist"
    if dist.is_dir():
        shutil.rmtree(dist)
    p = subprocess.run(
        ["uv", "build"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    names: list[str] = []
    if dist.is_dir():
        names = sorted(f.name for f in dist.iterdir() if f.is_file())
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "dist_files": names,
        "stdout": (p.stdout or "")[-8000:],
        "stderr": (p.stderr or "")[-8000:],
    }


def twine_upload(root: Path, repository_url: str | None = None) -> dict:
    cmd = ["twine", "upload", "dist/*"]
    env = None
    # twine expands glob itself when run via shell; subprocess needs explicit files
    dist = root / "dist"
    if not dist.is_dir():
        return {"ok": False, "error": "No dist/ directory — run uv_build first"}
    files = [str(f) for f in sorted(dist.iterdir()) if f.is_file()]
    if not files:
        return {"ok": False, "error": "dist/ is empty"}
    cmd = ["twine", "upload", *files]
    if repository_url:
        cmd.extend(["--repository-url", repository_url])
    p = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "uploaded": files,
        "stdout": (p.stdout or "")[-8000:],
        "stderr": (p.stderr or "")[-8000:],
    }


def release_notes_write(
    root: Path,
    content: str,
    path: str = "release_notes.md",
    mode: str = "replace",
) -> dict:
    """Create or update release_notes.md (append or replace)."""
    fp = root / path
    if mode not in ("replace", "append"):
        return {"ok": False, "error": "mode must be replace or append"}
    try:
        if mode == "append" and fp.is_file():
            prev = fp.read_text(encoding="utf-8")
            body = prev.rstrip() + "\n\n" + content.strip() + "\n"
        else:
            body = content.strip() + "\n"
        fp.write_text(body, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": str(fp.relative_to(root)), "mode": mode}
