"""Click CLI — deterministic release sequence with one LLM turn for diff analysis."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.theme import Theme

from rel_ease import __version__
from rel_ease import git_ops, release_build, version_bump
from rel_ease.assistant import DiffAnalysis, analyze_diff
from rel_ease.repo import RepoKind, detect_repo
from rel_ease.semver_util import bump_part

_theme = Theme(
    {
        "re.title": "bold bright_cyan",
        "re.accent": "bright_magenta",
        "re.dim": "dim",
        "re.ok": "green",
        "re.warn": "yellow",
        "re.step": "bold white",
    }
)
console = Console(theme=_theme)

_JUNK = {
    "dist", "__pycache__", ".venv", "venv", "node_modules",
    ".terraform", ".pytest_cache", ".ruff_cache",
}
_JUNK_EXTS = {".pyc", ".pyo", ".tfstate", ".log"}
_JUNK_NAMES = {".env", ".DS_Store"}


def _step(msg: str, status: str = "…") -> None:
    console.print(f"  [re.dim]{status}[/re.dim]  [re.step]{msg}[/re.step]")


def _ok(msg: str) -> None:
    console.print(f"  [re.ok]✓[/re.ok]  {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [re.warn]![/re.warn]  {msg}")


def _fail(msg: str) -> None:
    console.print(f"  [re.warn]✗[/re.warn]  {msg}")


def _is_junk(path_str: str) -> bool:
    p = Path(path_str)
    if p.suffix in _JUNK_EXTS:
        return True
    if p.name in _JUNK_NAMES:
        return True
    for part in p.parts:
        if part in _JUNK:
            return True
    return False


def _files_to_stage(
    root: Path,
    status_files: list[dict],
    version_file: Path | None,
    extra: list[Path],
) -> list[str]:
    """Compute paths to stage: modified/added tracked files + version file + extras."""
    staged: list[str] = []
    all_untracked = status_files and all(
        f.get("index_worktree", "  ").strip() == "??" for f in status_files
    )

    if all_untracked:
        # Initial commit: add everything except junk
        for f in status_files:
            p = f["path"]
            if not _is_junk(p):
                staged.append(p)
    else:
        # Add modified/added tracked files; skip untracked (only explicit extras added)
        for f in status_files:
            xy = f.get("index_worktree", "  ")
            work_tree = xy[1] if len(xy) > 1 else " "
            index = xy[0]
            p = f["path"]
            if work_tree in ("M", "A", "D") or index in ("M", "A"):
                if not _is_junk(p):
                    staged.append(p)

    # Always include version file and extras (release_notes.md etc.)
    always = [str(p.relative_to(root)) for p in extra if p.exists()]
    if version_file:
        vf_rel = str(version_file.relative_to(root))
        if vf_rel not in staged:
            always.append(vf_rel)
    for a in always:
        if a not in staged:
            staged.append(a)

    return staged


def main() -> None:
    cli(obj={})


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="rel-ease")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Rel-Ease — AI-assisted release manager for Python, Node, and Rust."""
    if ctx.invoked_subcommand is None:
        console.print(
            Panel.fit(
                "[re.title]Rel-Ease[/re.title] [re.dim]—[/re.dim] [re.accent]ship with confidence[/re.accent]\n"
                "[re.dim]Run[/re.dim] [bold]rel-ease release[/bold] [re.dim]inside a git repo, or[/re.dim] "
                "[bold]rel-ease doctor[/bold] [re.dim]to check tooling.[/re.dim]",
                border_style="bright_cyan",
            )
        )
        console.print(ctx.get_help())


@cli.command("doctor")
@click.argument("path", type=click.Path(path_type=Path, file_okay=False), default=".")
def doctor(path: Path) -> None:
    """Check API key, git, uv, twine, npm, cargo."""
    root = path.resolve()
    rows: list[tuple[str, str, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        icon = "[re.ok]●[/re.ok]" if ok else "[re.warn]○[/re.warn]"
        rows.append((icon, label, detail))

    check("BACKBOARD_API_KEY", bool(os.environ.get("BACKBOARD_API_KEY")))
    check("git repo", (root / ".git").is_dir(), str(root))
    for bin_name in ("git", "uv", "twine", "npm", "cargo"):
        check(bin_name, shutil.which(bin_name) is not None)

    table = Table(show_header=False, box=None, padding=(0, 2))
    for s, lab, det in rows:
        table.add_row(s, lab, f"[re.dim]{det}[/re.dim]" if det else "")
    console.print(Rule("[re.title]Doctor[/re.title]", style="bright_cyan"))
    console.print(table)

    ctx = detect_repo(root)
    console.print(
        f"\n[re.accent]Detected:[/re.accent] [bold]{ctx.kind.value}[/bold]"
        f"  [re.dim]version[/re.dim] {ctx.current_version or '—'}"
    )


@cli.command("detect")
@click.argument("path", type=click.Path(path_type=Path, file_okay=False), default=".")
def detect_cmd(path: Path) -> None:
    """Print detected repo kind and version (no API calls)."""
    ctx = detect_repo(path)
    console.print(ctx.summary(), end="")


@cli.command("release")
@click.argument("path", type=click.Path(path_type=Path, file_okay=False), default=".")
@click.option("--hint", type=str, default=None, help="Extra guidance for the LLM (version choice, notes tone, etc.).")
@click.option(
    "--assistant-id",
    type=str,
    default=None,
    envvar="REL_EASE_ASSISTANT_ID",
    help="Backboard assistant UUID override.",
)
@click.option("--dry-run", is_flag=True, help="Analyze only — no version bump, commit, build, or upload.")
@click.option("--publish/--no-publish", default=True, help="Run uv build + twine upload for Python (default: on).")
def release_cmd(
    path: Path,
    hint: str | None,
    assistant_id: str | None,
    dry_run: bool,
    publish: bool,
) -> None:
    """Analyze diff with Backboard, then run the full release sequence."""
    root = path.resolve()
    key = os.environ.get("BACKBOARD_API_KEY")
    if not key:
        raise click.ClickException("BACKBOARD_API_KEY not set (run: rel-ease doctor).")
    if not (root / ".git").is_dir():
        raise click.ClickException(f"Not a git repository: {root}")

    repo = detect_repo(root)
    console.print(
        Panel(
            f"[re.title]Rel-Ease[/re.title]  [re.dim]{root}[/re.dim]\n"
            f"[re.accent]{repo.kind.value}[/re.accent]  ·  "
            f"[re.dim]current version[/re.dim] {repo.current_version or '?'}",
            border_style="bright_magenta",
        )
    )

    # ── Step 1: read working tree ──────────────────────────────────────────
    _step("Reading git status + diff", "1")
    status = git_ops.git_status_porcelain(root)
    status_files = status.get("files", [])
    if not status_files:
        console.print(
            Panel(
                "[re.dim]Nothing to release — working tree is clean.[/re.dim]",
                border_style="yellow",
            )
        )
        return

    diff_result = git_ops.git_diff(root, stat=False)
    diff_text = diff_result.get("stdout", "")
    _ok(f"{len(status_files)} changed file(s)")

    # ── Step 2: LLM analysis ───────────────────────────────────────────────
    _step("Asking Backboard to analyze diff…", "2")
    try:
        analysis: DiffAnalysis = asyncio.run(
            analyze_diff(
                diff=diff_text,
                status_files=status_files,
                repo_kind=repo.kind.value,
                current_version=repo.current_version,
                hint=hint,
                api_key=key,
                assistant_id=assistant_id,
            )
        )
    except Exception as e:
        raise click.ClickException(f"Backboard error: {e}") from e

    part = analysis.semver_part if analysis.semver_part in ("patch", "minor", "major") else "patch"
    old_ver = repo.current_version or "0.0.0"
    new_ver = bump_part(old_ver, part)

    console.print(
        Panel(
            f"[re.dim]bump[/re.dim]   [bold]{old_ver}[/bold] → [re.ok]{new_ver}[/re.ok]  [re.dim]({part})[/re.dim]\n"
            f"[re.dim]why[/re.dim]    {analysis.reasoning}\n"
            f"[re.dim]commit[/re.dim] {analysis.commit_summary}",
            border_style="bright_cyan",
            title="[re.title]Decision[/re.title]",
        )
    )

    if dry_run:
        console.print(
            Panel(
                f"[re.warn]Dry run — stopping before mutations.[/re.warn]\n\n"
                f"[re.dim]Release notes preview:[/re.dim]\n{analysis.release_notes_md}",
                border_style="yellow",
                title="Dry Run",
            )
        )
        return

    # ── Step 3: bump version ───────────────────────────────────────────────
    _step(f"Bumping version → {new_ver}", "3")
    bump_result = version_bump.apply_bump(repo, part, None)
    if not bump_result.get("ok"):
        raise click.ClickException(f"Version bump failed: {bump_result.get('error')}")
    _ok(f"Updated {bump_result['file']}")

    if repo.kind == RepoKind.NODE:
        lock = version_bump.npm_install_package_lock_only(root)
        if lock.get("ok"):
            _ok("Refreshed package-lock.json")
        else:
            _warn("npm install --package-lock-only failed (continuing)")

    # ── Step 4: release notes ──────────────────────────────────────────────
    _step("Writing release_notes.md", "4")
    notes_path = root / "release_notes.md"
    mode = "append" if notes_path.exists() else "replace"
    header = f"## v{new_ver}\n\n"
    notes_body = header + analysis.release_notes_md.strip()
    notes_result = release_build.release_notes_write(root, notes_body, mode=mode)
    if not notes_result.get("ok"):
        _warn(f"Release notes: {notes_result.get('error')}")
    else:
        _ok(f"release_notes.md ({mode}d)")

    # ── Step 5: git add ────────────────────────────────────────────────────
    _step("Staging files", "5")
    extra_paths = [root / "release_notes.md"]
    if repo.kind == RepoKind.NODE:
        extra_paths.append(root / "package-lock.json")
    to_stage = _files_to_stage(root, status_files, repo.version_file, extra_paths)
    if not to_stage:
        raise click.ClickException("Nothing to stage — aborting.")
    add_result = git_ops.git_add(root, to_stage)
    if not add_result.get("ok"):
        raise click.ClickException(f"git add failed:\n{add_result.get('stderr')}")
    _ok(f"Staged {len(to_stage)} file(s)")

    # ── Step 6: git commit ─────────────────────────────────────────────────
    _step("Committing", "6")
    label = part.capitalize()
    commit_msg = f"v{new_ver} {label}: {analysis.commit_summary}"
    commit_result = git_ops.git_commit(root, commit_msg)
    if not commit_result.get("ok"):
        raise click.ClickException(f"git commit failed:\n{commit_result.get('stderr')}")
    _ok(commit_msg)

    # ── Step 7: Python publish ─────────────────────────────────────────────
    if repo.kind == RepoKind.PYTHON and publish:
        _step("Building package (uv build)", "7")
        build = release_build.uv_build(root)
        if not build.get("ok"):
            _fail("uv build failed")
            console.print(build.get("stderr", "")[-3000:])
            raise click.ClickException("Build failed — not uploading.")
        _ok(f"Built: {', '.join(build.get('dist_files', []))}")

        _step("Publishing to PyPI (twine upload)", "8")
        upload = release_build.twine_upload(root)
        if not upload.get("ok"):
            _fail("twine upload failed")
            console.print(upload.get("stderr", "")[-3000:])
            raise click.ClickException("Upload failed.")
        _ok("Uploaded to PyPI")

    # ── Done ───────────────────────────────────────────────────────────────
    console.print(Rule("[re.title]Release Notes[/re.title]", style="bright_cyan"))
    console.print(Markdown(notes_body))

    next_steps = ["Push: `git push`", "Tag: `git tag v{} && git push --tags`".format(new_ver)]
    if repo.kind == RepoKind.RUST:
        next_steps.append("Publish crate: `cargo publish`")
    if repo.kind == RepoKind.PYTHON and not publish:
        next_steps.append("Publish: `rel-ease release --publish` or `uv build && twine upload dist/*`")

    console.print(
        Panel(
            "[re.ok]✓ Done.[/re.ok]  Next:\n" + "\n".join(f"  • {s}" for s in next_steps),
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
