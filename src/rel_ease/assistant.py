"""Single-turn ai-layer calls for release-safe diff analysis."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from ai_layer.client import AILayerClient

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

DIFF_DESCRIBER_NAME = "rel-ease-diff-describer"
DIFF_DESCRIBER_PROMPT = """\
You are an expert release engineer. You will receive git status plus staged and unstaged git diffs.
Summarize what changed in user-facing release language. Do not merely list filenames.

Return ONLY a single valid JSON object with exactly this shape:
  summary_md: a detailed markdown summary with 2-5 bullets
  release_notes_md: markdown bullet list suitable for release notes
  commit_summary: concise 5-10 word summary
  risk_notes: markdown bullet list of risks or "None"

Rules:
- Explain the behavior or capability changed, not just files changed.
- Ignore pure version bumps, lockfile version syncs, build artifacts, and generated metadata unless they are the only change.
- If the diff only contains package rename/version/lock metadata, say that clearly.
- Do not invent implementation details not supported by the diff.
- Respond with ONLY the JSON object. Do not wrap it in markdown fences.
"""


@dataclass
class DiffAnalysis:
    semver_part: str
    commit_summary: str
    release_notes_md: str
    reasoning: str


@dataclass
class DiffDescription:
    summary_md: str
    release_notes_md: str
    commit_summary: str
    risk_notes: str


def _extract_json(text: str) -> dict:
    """Strip markdown fences if the model added them anyway."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    m2 = re.search(r"\{.*\}", text, re.DOTALL)
    if m2:
        text = m2.group(0)
    return json.loads(text)


async def _get_or_create_agent(
    client: AILayerClient,
    agent_id_hint: str | None,
    name: str = ASSISTANT_NAME,
    system_prompt: str = SYSTEM_PROMPT,
    env_var: str = "REL_EASE_AGENT_ID",
) -> str:
    if agent_id_hint:
        return agent_id_hint
    env = os.environ.get(env_var)
    if env:
        return env
    for a in await client.list_agents():
        if a.get("name") == name:
            await client.update_agent(
                a["id"],
                system_prompt=system_prompt,
                tools=[],
                builtin_tools=[],
            )
            return a["id"]
    created = await client.create_agent(
        name=name,
        model=os.environ.get("REL_EASE_MODEL", "anthropic/claude-sonnet-4-6"),
        system_prompt=system_prompt,
        tools=[],
        builtin_tools=[],
    )
    return created["id"]


def _escape_braces(text: str) -> str:
    """Escape curly braces so LangChain prompt templates don't treat them as variables."""
    return text.replace("{", "{{").replace("}", "}}")


def _ai_layer_base_url() -> str:
    base_url = os.environ.get("AI_LAYER_URL", "http://localhost:8000/api").rstrip("/")
    if base_url.endswith("/api"):
        return base_url
    return f"{base_url}/api"


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
    client = AILayerClient(base_url=_ai_layer_base_url(), api_key=api_key, timeout_s=120)
    agent_id = await _get_or_create_agent(client, assistant_id)
    thread = await client.create_thread(agent_id=agent_id)
    thread_id = thread["id"]
    prompt = _build_prompt(diff, status_files, repo_kind, current_version, hint)
    raw, _ = await client.collect_text(
        agent_id=agent_id,
        thread_id=thread_id,
        message={"role": "user", "content": prompt},
    )
    raw = raw.strip()
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


def _build_description_prompt(
    status_files: list[dict],
    repo_kind: str,
    current_version: str | None,
    staged_diff: str,
    unstaged_diff: str,
) -> str:
    status_lines = "\n".join(f"  {item.get('raw', item.get('path', ''))}" for item in status_files[:120])
    staged_body = staged_diff.strip() or "(none)"
    unstaged_body = unstaged_diff.strip() or "(none)"
    if len(staged_body) > 16_000:
        staged_body = staged_body[:16_000] + "\n...(truncated)"
    if len(unstaged_body) > 16_000:
        unstaged_body = unstaged_body[:16_000] + "\n...(truncated)"
    return "\n".join(
        [
            f"repo_kind: {repo_kind}",
            f"current_version: {current_version or 'none'}",
            "",
            "git status:",
            status_lines or "  clean",
            "",
            "staged git diff:",
            _escape_braces(staged_body),
            "",
            "unstaged git diff:",
            _escape_braces(unstaged_body),
            "",
            "Respond with ONLY the JSON object.",
        ]
    )


async def describe_diff_with_llm(
    status_files: list[dict],
    repo_kind: str,
    current_version: str | None,
    staged_diff: str,
    unstaged_diff: str,
    api_key: str,
    assistant_id: str | None = None,
) -> DiffDescription:
    client = AILayerClient(base_url=_ai_layer_base_url(), api_key=api_key, timeout_s=120)
    agent_id = await _get_or_create_agent(
        client,
        assistant_id,
        name=DIFF_DESCRIBER_NAME,
        system_prompt=DIFF_DESCRIBER_PROMPT,
        env_var="REL_EASE_DIFF_AGENT_ID",
    )
    thread = await client.create_thread(agent_id=agent_id)
    raw, _ = await client.collect_text(
        agent_id=agent_id,
        thread_id=thread["id"],
        message={
            "role": "user",
            "content": _build_description_prompt(
                status_files=status_files,
                repo_kind=repo_kind,
                current_version=current_version,
                staged_diff=staged_diff,
                unstaged_diff=unstaged_diff,
            ),
        },
    )
    raw = raw.strip()
    try:
        data = _extract_json(raw)
    except (json.JSONDecodeError, AttributeError) as e:
        raise ValueError(f"LLM returned non-JSON:\n{raw}") from e
    return DiffDescription(
        summary_md=str(data.get("summary_md", "")).strip(),
        release_notes_md=_normalise_notes(data.get("release_notes_md", "")),
        commit_summary=str(data.get("commit_summary", "")).strip(),
        risk_notes=_normalise_notes(data.get("risk_notes", "")),
    )


def _normalise_notes(raw: object) -> str:
    """Coerce whatever the LLM returned into clean markdown bullet lines."""
    if isinstance(raw, list):
        bullets = raw
    elif isinstance(raw, str):
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
