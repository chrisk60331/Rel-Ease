"""Backboard assistant: Rel-Ease release orchestration."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Union

from backboard import BackboardClient
from backboard.models import MessageResponse, ToolCall, ToolCallFunction, ToolOutputsResponse

from rel_ease.repo import RepoContext, detect_repo
from rel_ease.tool_handlers import initial_user_message, make_dispatcher

ASSISTANT_NAME = "rel-ease"

SYSTEM_PROMPT = """You are Rel-Ease, an opinionated release engineer. You act — you do not ask.

## Core rule: full send
Execute the complete release sequence using tools without stopping to ask the user questions or for confirmation. Make decisions yourself. If something is ambiguous, pick the safer choice (patch over minor, no secrets staged) and proceed.

## Version bump heuristics
- **patch** — bug fixes, doc tweaks, single-file logic changes, copy edits
- **minor** — new user-facing features, new routes/commands, multi-file additions
- **major** — only for clear breaking API or interface changes visible in the diff
- All files untracked (fresh repo / first commit): default to **minor** unless the hint says otherwise.

## Standard flow — execute every step, in order, with tools
1. `git_status_files` + `git_diff` — read the working tree.
2. `increment_version` — choose patch/minor/major, bump immediately.
3. `release_notes_update` — write tight Markdown bullets (user-facing tone, mode `replace` first time, `append` for subsequent releases).
4. `git_add` — stage version file, lockfile if any, release_notes.md, and any modified source files that belong to the release. Skip `.env`, `dist/`, `__pycache__`, secrets.
5. `git_commit` — commit pattern `vX.Y.Z Minor|Patch|Major: short summary`.
6. For **Python** only: `uv_build` then `twine_upload`. If build/upload fails, report stderr and stop.
7. Done — print a brief summary.

## Never do these things
- Do NOT ask "do you want to proceed?" — just proceed.
- Do NOT ask the user to confirm the version bump.
- Do NOT explain what you are about to do before doing it.
- Do NOT stage `.env`, `dist/`, `*.tfstate`, `__pycache__`, private keys, or anything that looks like a secret.
- Do NOT call `uv_build` or `twine_upload` for Node or Rust repos.
- If the user message starts with `DRY RUN`, use only read-only tools (`get_repo_context`, `git_status_files`, `git_diff`) — read and report, never mutate.

## Repo-specific notes
- **Node**: `increment_version` refreshes package-lock.json automatically.
- **Rust**: after the commit, remind the user to run `cargo publish` (no crates.io tool here).

## Response style
Between tool rounds: one sentence max. Final reply: 3–6 bullet summary (version, what shipped, next steps like push/tag/GitHub release/cargo publish).
"""


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_repo_context",
                "description": "Refresh repo kind (python, node_ts, rust), version file path, and current version.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "increment_version",
                "description": "Bump semver in pyproject.toml, package.json, or Cargo.toml [package].",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "semver_part": {
                            "type": "string",
                            "enum": ["patch", "minor", "major"],
                            "description": "Which segment to increment",
                        },
                        "explicit_version": {
                            "type": "string",
                            "description": "If set, use this exact X.Y.Z instead of bumping",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_diff",
                "description": "Show git diff, optionally limited to paths.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "stat": {
                            "type": "boolean",
                            "description": "If true, use diff --stat (default true)",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional path filters",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_status_files",
                "description": "Per-file git status (porcelain), including untracked.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_add",
                "description": "Stage specific paths (not glob-all).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["paths"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_commit",
                "description": "Create a commit with the given message.",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "uv_build",
                "description": "Python only: clean dist/ and run `uv build`.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "release_notes_update",
                "description": "Create or update release_notes.md (or another path).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Markdown body"},
                        "path": {
                            "type": "string",
                            "description": "Relative to repo root, default release_notes.md",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "twine_upload",
                "description": "Python only: upload dist/* with twine (uses env credentials).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repository_url": {
                            "type": "string",
                            "description": "Optional alternate index URL",
                        },
                    },
                },
            },
        },
    ]


def _needs_action(status: str | None) -> bool:
    return bool(status and status.upper() == "REQUIRES_ACTION")


def _coerce_tool_calls(raw: Any) -> list[ToolCall]:
    if not raw:
        return []
    out: list[ToolCall] = []
    for item in raw:
        if isinstance(item, ToolCall):
            out.append(item)
            continue
        if isinstance(item, dict):
            fn = item.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, dict):
                args = json.dumps(args)
            elif args is None:
                args = "{}"
            out.append(
                ToolCall(
                    id=str(item.get("id", "")),
                    type=str(item.get("type", "function")),
                    function=ToolCallFunction(
                        name=str(fn.get("name", "")),
                        arguments=str(args),
                    ),
                )
            )
    return out


async def get_or_create_assistant(
    client: BackboardClient,
    assistant_id_env: str | None,
) -> uuid.UUID:
    if assistant_id_env:
        try:
            return uuid.UUID(assistant_id_env)
        except ValueError:
            pass
    stored = os.environ.get("REL_EASE_ASSISTANT_ID")
    if stored:
        try:
            return uuid.UUID(stored)
        except ValueError:
            pass
    assistants = await client.list_assistants()
    for a in assistants:
        if a.name == ASSISTANT_NAME:
            await client.update_assistant(
                a.assistant_id,
                system_prompt=SYSTEM_PROMPT,
                tools=tool_schemas(),
            )
            return a.assistant_id
    created = await client.create_assistant(
        name=ASSISTANT_NAME,
        description="Rel-Ease CLI release manager",
        system_prompt=SYSTEM_PROMPT,
        tools=tool_schemas(),
    )
    return created.assistant_id


async def _submit_tools(
    client: BackboardClient,
    thread_id: uuid.UUID,
    run_id: str,
    tool_calls: list[ToolCall],
    dispatch: Callable[[str, dict[str, Any]], str],
    on_tool: Callable[[str, str], None] | None,
) -> Union[MessageResponse, ToolOutputsResponse]:
    outputs = []
    for tc in tool_calls:
        name = tc.function.name
        args = tc.function.parsed_arguments
        result = dispatch(name, args)
        if on_tool:
            on_tool(name, result)
        outputs.append({"tool_call_id": tc.id, "output": result})
    return await client.submit_tool_outputs(
        thread_id=thread_id,
        run_id=run_id,
        tool_outputs=outputs,
        stream=False,
    )


async def _loop_tools(
    client: BackboardClient,
    thread_id: uuid.UUID,
    response: Union[MessageResponse, ToolOutputsResponse],
    dispatch: Callable[[str, dict[str, Any]], str],
    on_tool: Callable[[str, str], None] | None,
) -> Union[MessageResponse, ToolOutputsResponse]:
    while _needs_action(response.status) and (tcs := _coerce_tool_calls(response.tool_calls)):
        rid = response.run_id
        if not rid:
            raise RuntimeError("REQUIRES_ACTION but run_id is missing")
        response = await _submit_tools(client, thread_id, rid, tcs, dispatch, on_tool)
    return response


async def run_release(
    root: Path,
    *,
    api_key: str,
    assistant_id: str | None,
    user_hint: str | None,
    on_tool: Callable[[str, str], None] | None = None,
    on_text: Callable[[str], None] | None = None,
) -> str:
    root = root.resolve()
    ctx = detect_repo(root)
    client = BackboardClient(api_key=api_key)
    aid = await get_or_create_assistant(client, assistant_id)
    thread = await client.create_thread(aid)
    dispatch = make_dispatcher(root)
    msg = initial_user_message(ctx, user_hint)
    response = await client.add_message(thread.thread_id, msg, stream=False)
    response = await _loop_tools(client, thread.thread_id, response, dispatch, on_tool)
    text = response.content or ""
    if on_text and text:
        on_text(text)
    return text
