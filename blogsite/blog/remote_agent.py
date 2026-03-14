import json
import re
from collections import OrderedDict

from django.conf import settings

from .openai_client import OpenAIRequestError, create_text_response
from .remote_executor import RemoteExecutorError


class RemotePlanningError(Exception):
    pass


INTENT_CHAT = "chat"
INTENT_REPO = "repo"
CHAT_PREFIX_PATTERN = re.compile(r"^/(chat|ask)\s*", re.IGNORECASE)
REPO_PREFIX_PATTERN = re.compile(r"^/(repo|task|code)\s*", re.IGNORECASE)


def _extract_json_object(text):
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RemotePlanningError("Model did not return JSON.")
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RemotePlanningError("Model returned invalid JSON.") from exc


def _repo_index_text(files):
    if not files:
        return "(repository file index is empty)"
    return "\n".join(f"- {path}" for path in files)


def _inspected_files_text(inspected_files):
    if not inspected_files:
        return "(no files inspected)"

    chunks = []
    for path, payload in inspected_files.items():
        content = payload["content"]
        suffix = "\n[TRUNCATED]" if payload.get("truncated") else ""
        chunks.append(f"FILE: {path}\n```text\n{content}\n```{suffix}")
    return "\n\n".join(chunks)


def _call_json_model(prompt, reasoning_effort="medium", verbosity="low"):
    response = create_text_response(
        prompt,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        instructions=settings.REMOTE_AGENT_SYSTEM_PROMPT,
    )
    return _extract_json_object(response["text"])


def strip_mode_prefix(user_message):
    text = (user_message or "").strip()
    if CHAT_PREFIX_PATTERN.match(text):
        return INTENT_CHAT, CHAT_PREFIX_PATTERN.sub("", text, count=1).strip()
    if REPO_PREFIX_PATTERN.match(text):
        return INTENT_REPO, REPO_PREFIX_PATTERN.sub("", text, count=1).strip()
    return None, text


def classify_user_request(user_message):
    forced_mode, cleaned_message = strip_mode_prefix(user_message)
    if forced_mode:
        return {
            "mode": forced_mode,
            "message": cleaned_message,
            "reason": "explicit prefix",
        }

    prompt = f"""
Return strict JSON only.

Schema:
{{
  "mode": "chat" | "repo",
  "reason": "short English reason"
}}

Rules:
- Use "chat" for greetings, personal questions, conceptual discussions, product questions,
  tool explanations, casual conversation, writing help, and anything answerable without
  reading the repository or touching the server.
- Use "repo" only when the user clearly wants repository-specific debugging, code changes,
  deployment/server checks, file inspection, test execution, or log analysis.
- If the request is ambiguous, prefer "chat".

User request:
{cleaned_message}
"""
    try:
        payload = _call_json_model(prompt, reasoning_effort="minimal", verbosity="low")
    except (RemotePlanningError, OpenAIRequestError):
        return {
            "mode": INTENT_CHAT,
            "message": cleaned_message,
            "reason": "classification fallback",
        }

    mode = payload.get("mode")
    if mode not in {INTENT_CHAT, INTENT_REPO}:
        mode = INTENT_CHAT

    return {
        "mode": mode,
        "message": cleaned_message,
        "reason": (payload.get("reason") or "").strip(),
    }


def answer_general_question(user_message):
    _, cleaned_message = strip_mode_prefix(user_message)
    if not cleaned_message:
        raise RemotePlanningError("General question is empty.")

    response = create_text_response(
        cleaned_message,
        reasoning_effort="low",
        verbosity="medium",
        instructions=(
            "You are linuxclaw, a Feishu AI assistant. "
            "Reply in concise Chinese by default. "
            "Answer directly without saying you will inspect the repository unless the user explicitly asks for project or server work. "
            "If the user asks what you can do, explain that normal Q&A is answered directly, while repository/server tasks will first produce a plan and then wait for approval."
        ),
    )
    return response["text"]


def select_files_for_request(user_message, repo_files, git_status):
    prompt = f"""
Return strict JSON only.

Schema:
{{
  "mode": "answer_only" | "inspect",
  "reply": "short Chinese reply for the user",
  "files_to_read": ["relative/path"],
  "notes": ["short note"]
}}

Rules:
- Choose at most 6 files.
- Only choose files from the repo index.
- If the user asks for a code change, debugging help, deployment analysis, or test failure analysis, use "inspect".
- If the request can be answered without repo inspection, use "answer_only".
- Prefer entry points, config, routing, and tests.

User request:
{user_message}

Git status:
{git_status or "(clean)"}

Repo index:
{_repo_index_text(repo_files)}
"""
    payload = _call_json_model(prompt)
    files_to_read = [
        path for path in payload.get("files_to_read", []) if path in set(repo_files)
    ][:6]
    return {
        "mode": payload.get("mode") or "inspect",
        "reply": payload.get("reply", "").strip(),
        "files_to_read": files_to_read,
        "notes": payload.get("notes", []),
    }


def draft_change_plan(user_message, git_status, inspected_files):
    prompt = f"""
Return strict JSON only.

Schema:
{{
  "summary": "one-line Chinese summary",
  "reply": "short Chinese reply for the user",
  "edits": [
    {{
      "type": "replace_text" | "write_file",
      "path": "relative/path",
      "search": "exact existing text when type=replace_text",
      "replace": "replacement text when type=replace_text",
      "content": "full file content when type=write_file"
    }}
  ],
  "tests": ["safe test command"],
  "risks": ["short note"],
  "additional_files_to_read": ["relative/path"]
}}

Rules:
- Keep edits minimal and reversible.
- Use "replace_text" whenever modifying an existing file.
- The "search" field must exactly match a unique span from the provided file content.
- Only use "write_file" for new files or complete rewrites that are clearly safer than search/replace.
- If you need more context, return no edits and set "additional_files_to_read".
- Keep tests limited to safe commands like pytest or python manage.py test.
- If the best response is only an explanation, return no edits and a useful "reply".

User request:
{user_message}

Git status:
{git_status or "(clean)"}

Inspected files:
{_inspected_files_text(inspected_files)}
"""
    payload = _call_json_model(prompt)
    return {
        "summary": (payload.get("summary") or "").strip(),
        "reply": (payload.get("reply") or "").strip(),
        "edits": payload.get("edits", []),
        "tests": payload.get("tests", []),
        "risks": payload.get("risks", []),
        "additional_files_to_read": payload.get("additional_files_to_read", []),
    }


def build_change_request_plan(executor, user_message):
    repo_files = executor.list_files()
    git_status = executor.git_status()
    selection = select_files_for_request(user_message, repo_files, git_status)
    if selection["mode"] == "answer_only":
        return {
            "summary": selection["reply"] or "The request does not need a repository change.",
            "reply": selection["reply"],
            "edits": [],
            "tests": [],
            "risks": selection["notes"],
        }

    inspected_files = OrderedDict()
    pending_paths = list(selection["files_to_read"])

    for _ in range(2):
        for path in pending_paths:
            if path in inspected_files:
                continue
            inspected_files[path] = executor.read_file(path)

        draft = draft_change_plan(user_message, git_status, inspected_files)
        additional = [
            path
            for path in draft["additional_files_to_read"]
            if path in set(repo_files) and path not in inspected_files
        ][:4]
        if draft["edits"] or not additional:
            draft["inspected_files"] = list(inspected_files.keys())
            draft["git_status"] = git_status
            return draft
        pending_paths = additional

    raise RemotePlanningError("Could not produce a stable change plan.")


def format_plan_for_user(plan, token):
    lines = []
    if plan.get("summary"):
        lines.append(f"计划: {plan['summary']}")
    if plan.get("reply"):
        lines.append(plan["reply"])
    paths = []
    for edit in plan.get("edits", []):
        path = edit.get("path")
        if path and path not in paths:
            paths.append(path)
    if paths:
        lines.append("拟改文件: " + ", ".join(paths))
    tests = [item for item in plan.get("tests", []) if item]
    if tests:
        lines.append("拟运行测试: " + " | ".join(tests[:2]))
    risks = [item for item in plan.get("risks", []) if item]
    if risks:
        lines.append("注意: " + " ; ".join(risks[:2]))
    if paths:
        lines.append(f"审批码: {token}")
        lines.append(f"回复 /approve {token} 执行，或 /reject {token} 取消。")
    return "\n".join(lines)


def _rollback_changes(executor, backups, created_files):
    for path in created_files:
        executor.delete_file(path)
    for path, original in backups.items():
        executor.write_file(path, original)


def apply_change_plan(executor, plan):
    edits = plan.get("edits", [])
    if not edits:
        return {
            "summary": plan.get("summary") or "No code changes were required.",
            "tests": [],
            "diff": "",
            "files": [],
        }

    executor.assert_clean_worktree()
    backups = OrderedDict()
    created_files = []
    touched_files = []
    test_outputs = []

    try:
        for edit in edits:
            edit_type = edit.get("type")
            path = edit.get("path")
            if not path:
                raise RemotePlanningError("Planned edit is missing a path.")
            if path not in touched_files:
                touched_files.append(path)

            if edit_type == "replace_text":
                if path not in backups:
                    backups[path] = executor.read_full_file(path)
                executor.replace_text(path, edit.get("search", ""), edit.get("replace", ""))
                continue

            if edit_type == "write_file":
                exists = executor.path_exists(path)
                if exists:
                    raise RemotePlanningError(
                        f"write_file may only create new files in this MVP: {path}"
                    )
                if path not in created_files:
                    created_files.append(path)
                executor.write_file(path, edit.get("content", ""))
                continue

            raise RemotePlanningError(f"Unsupported edit type: {edit_type}")

        for command in plan.get("tests", []):
            output = executor.run_test_command(command)
            test_outputs.append({"command": command, "output": output.strip()})
    except (RemoteExecutorError, RemotePlanningError, OpenAIRequestError):
        _rollback_changes(executor, backups, created_files)
        raise

    return {
        "summary": plan.get("summary") or "Applied remote change plan.",
        "tests": test_outputs,
        "diff": executor.git_diff(touched_files),
        "files": touched_files,
    }
