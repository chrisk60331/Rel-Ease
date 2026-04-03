"""Single-turn Backboard call: analyze diff → version decision + release notes."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from backboard import BackboardClient

ASSISTANT_NAME = "rel-ease"

SYSTEM_PROMPT = """\
You are an expert release engineer. You will receive a git diff (or file list for initial commits) \
and respond with ONLY a single valid JSON object — no prose, no markdown fences.

Return exactly this shape (replace angle-bracket placeholders with real values):
  semver_part: one of patch, minor, or major
  commit_summary: concise 5-10 word summary
  release_notes_md: markdown bullet list, user-facing tone
  reasoning: one sentence explaining the semver choice

Version bump heuristics:
- patch: bug fixes, docs, copy edits, single-file tweaks
- minor: new features, new commands/APIs, multi-file additions
- major: breaking interface/API changes clearly visible in the diff
- Initial commit / all files new: default to minor

Respond with ONLY the JSON object. Do not wrap it in markdown fences.
"""


@dataclass
class DiffAnalysis:
    semver_part: str
    commit_summary: str
    release_notes_md: str
    reasoning: str


def _extract_json(text: str) -> dict:
    """Strip markdown fences if the model added them anyway."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Try to find the first {...} block
    m2 = re.search(r"\{.*\}", text, re.DOTALL)
    if m2:
        text = m2.group(0)
    return json.loads(text)


async def _get_or_create_assistant(client: BackboardClient, aid_str: str | None) -> uuid.UUID:
    if aid_str:
        try:
            return uuid.UUID(aid_str)
        except ValueError:
            pass
    env = os.environ.get("REL_EASE_ASSISTANT_ID")
    if env:
        try:
            return uuid.UUID(env)
        except ValueError:
            pass
    for a in await client.list_assistants():
        if a.name == ASSISTANT_NAME:
            # Keep system prompt fresh, no tools needed
            await client.update_assistant(
                a.assistant_id,
                system_prompt=SYSTEM_PROMPT,
                tools=[],
            )
            return a.assistant_id
    created = await client.create_assistant(
        name=ASSISTANT_NAME,
        description="Rel-Ease diff analyzer",
        system_prompt=SYSTEM_PROMPT,
        tools=[],
    )
    return created.assistant_id


def _escape_braces(text: str) -> str:
    """Escape curly braces so LangChain prompt templates don't treat them as variables."""
    return text.replace("{", "{{").replace("}", "}}")


def _build_prompt(
    diff: str,
    status_files: list[dict],
    repo_kind: str,
    current_version: str | None,
    hint: str | None,
) -> str:
    all_untracked = status_files and all(
        f.get("index_worktree", "  ").strip() == "??" for f in status_files
    )
    has_diff = bool(diff and diff.strip())

    lines = [
        f"repo_kind: {repo_kind}",
        f"current_version: {current_version or 'none'}",
    ]

    if all_untracked or not has_diff:
        file_names = [f["path"] for f in status_files[:80]]
        lines.append(
            f"\nInitial commit or no tracked changes. New/untracked files ({len(status_files)} total):\n"
            + "\n".join(f"  {n}" for n in file_names)
        )
    else:
        diff_body = diff if len(diff) <= 12_000 else diff[:12_000] + "\n…(truncated)"
        lines.append(f"\ngit diff:\n{_escape_braces(diff_body)}")

    if hint:
        lines.append(f"\nUser hint: {_escape_braces(hint)}")

    lines.append("\nRespond with ONLY the JSON object.")
    return "\n".join(lines)


async def analyze_diff(
    diff: str,
    status_files: list[dict],
    repo_kind: str,
    current_version: str | None,
    hint: str | None,
    api_key: str,
    assistant_id: str | None,
) -> DiffAnalysis:
    client = BackboardClient(api_key=api_key, timeout=120)
    aid = await _get_or_create_assistant(client, assistant_id)
    thread = await client.create_thread(aid)
    prompt = _build_prompt(diff, status_files, repo_kind, current_version, hint)
    response = await client.add_message(thread.thread_id, prompt, stream=False)
    raw = (response.content or "").strip()
    try:
        data = _extract_json(raw)
    except (json.JSONDecodeError, AttributeError) as e:
        raise ValueError(f"LLM returned non-JSON:\n{raw}") from e

    return DiffAnalysis(
        semver_part=str(data.get("semver_part", "patch")).lower().strip(),
        commit_summary=str(data.get("commit_summary", "release")),
        release_notes_md=_normalise_notes(data.get("release_notes_md", "")),
        reasoning=str(data.get("reasoning", "")),
    )


def _normalise_notes(raw: object) -> str:
    """Coerce whatever the LLM returned into clean markdown bullet lines."""
    if isinstance(raw, list):
        bullets = raw
    elif isinstance(raw, str):
        # Try to parse as JSON array in case model returned '["a","b"]'
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                bullets = json.loads(stripped)
                if not isinstance(bullets, list):
                    return stripped
            except json.JSONDecodeError:
                return stripped
        else:
            return stripped
    else:
        return str(raw)
    return "\n".join(f"- {str(b).strip().lstrip('- ')}" for b in bullets if b)
