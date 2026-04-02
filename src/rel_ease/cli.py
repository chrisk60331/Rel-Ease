"""Click CLI — delightful terminal UX for Rel-Ease."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.syntax import Syntax
from rich.theme import Theme

from rel_ease import __version__
from rel_ease.assistant import run_release
from rel_ease.repo import detect_repo

_theme = Theme(
    {
        "re.title": "bold bright_cyan",
        "re.accent": "bright_magenta",
        "re.dim": "dim",
        "re.ok": "green",
        "re.warn": "yellow",
    }
)
console = Console(theme=_theme)


def main() -> None:
    cli(obj={})


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="rel-ease")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Rel-Ease — AI release manager (Backboard + your repo)."""
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
    """Check Backboard API key, git, uv, twine, npm, cargo."""
    root = path.resolve()
    rows: list[tuple[str, str, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        status = "[re.ok]●[/re.ok]" if ok else "[re.warn]○[/re.warn]"
        rows.append((status, label, detail))

    check("BACKBOARD_API_KEY", bool(os.environ.get("BACKBOARD_API_KEY")))
    check("git repo", (root / ".git").is_dir(), str(root))
    for bin_name, label in (
        ("git", "git"),
        ("uv", "uv"),
        ("twine", "twine"),
        ("npm", "npm"),
        ("cargo", "cargo"),
    ):
        check(label, shutil.which(bin_name) is not None)

    from rich.table import Table

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


@cli.command("release")
@click.argument("path", type=click.Path(path_type=Path, file_okay=False), default=".")
@click.option("--hint", type=str, default=None, help="Extra guidance for the assistant.")
@click.option(
    "--assistant-id",
    type=str,
    default=None,
    envvar="REL_EASE_ASSISTANT_ID",
    help="Backboard assistant UUID (optional; uses named assistant rel-ease by default).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Read-only: assistant inspects repo but must not mutate or publish.",
)
def release_cmd(
    path: Path,
    hint: str | None,
    assistant_id: str | None,
    dry_run: bool,
) -> None:
    """Run the Backboard release assistant (tools: git, bump, notes, uv, twine)."""
    root = path.resolve()
    key = os.environ.get("BACKBOARD_API_KEY")
    if not key:
        raise click.ClickException(
            "Set BACKBOARD_API_KEY in your environment (see rel-ease doctor)."
        )
    if not (root / ".git").is_dir():
        raise click.ClickException(f"Not a git repository: {root}")

    ctx = detect_repo(root)
    console.print(
        Panel(
            f"[re.title]Release[/re.title]  [re.dim]{root}[/re.dim]\n"
            f"[re.accent]{ctx.kind.value}[/re.accent]  ·  "
            f"[re.dim]version[/re.dim] {ctx.current_version or 'unknown'}",
            border_style="bright_magenta",
        )
    )

    combined_hint = hint
    if dry_run:
        combined_hint = (
            "DRY RUN: only read-only tools. Do not bump, commit, edit release notes, build, or upload.\n"
            + (hint or "")
        ).strip()

    tool_lines: list[str] = []

    def on_tool(name: str, payload: str) -> None:
        tool_lines.append(f"[bold cyan]{name}[/bold cyan]")
        try:
            if len(payload) > 1200:
                preview = payload[:1200] + "\n…"
            else:
                preview = payload
            console.print(
                Panel(
                    Syntax(preview, "json", theme="monokai", background_color="default"),
                    title=f"tool · {name}",
                    border_style="cyan",
                    subtitle="[dim]truncated if long[/dim]",
                )
            )
        except Exception:
            console.print(Panel(payload[:2000], title=name, border_style="cyan"))

    final_text: list[str] = []

    def on_text(t: str) -> None:
        final_text.append(t)

    async def _go() -> str:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Talking to Backboard…", total=None)
            return await run_release(
                root,
                api_key=key,
                assistant_id=assistant_id,
                user_hint=combined_hint,
                on_tool=on_tool,
                on_text=on_text,
            )

    try:
        summary = asyncio.run(_go())
    except Exception as e:
        raise click.ClickException(str(e)) from e

    if summary:
        console.print(Rule("[re.title]Assistant[/re.title]", style="bright_magenta"))
        console.print(Markdown(summary))
    console.print(
        Panel(
            "[re.ok]Run complete.[/re.ok] [re.dim]Push, tag, and GitHub release are still up to you unless you automate them elsewhere.[/re.dim]",
            border_style="green",
        )
    )


@cli.command("detect")
@click.argument("path", type=click.Path(path_type=Path, file_okay=False), default=".")
def detect_cmd(path: Path) -> None:
    """Print detected repo kind and version (no API calls)."""
    ctx = detect_repo(path)
    console.print(ctx.summary(), end="")


if __name__ == "__main__":
    main()
