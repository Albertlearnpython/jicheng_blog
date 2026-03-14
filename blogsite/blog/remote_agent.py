import json
import re
import shlex
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
ACTION_HINTS = ("查", "看", "看看", "看下", "看一下", "查询", "多少", "多大", "占用", "使用", "状态", "情况", "信息", "列出", "显示")
DISK_HINTS = ("磁盘", "硬盘", "存储", "空间", "容量", "disk", "storage")
MEMORY_HINTS = ("内存", "memory", "ram")
CPU_HINTS = ("cpu", "负载", "load", "核心")
DOCKER_HINTS = ("docker", "容器", "container")
PORT_HINTS = ("端口", "监听", "listen", "socket")
PROCESS_HINTS = ("进程", "process", "线程")
DIRECTORY_HINTS = ("目录", "文件夹", "路径")
ABSOLUTE_PATH_PATTERN = re.compile(r"(/[A-Za-z0-9._/\-]+)")
HIGH_RISK_KEYWORDS = (
    "删除",
    "清空",
    "重置",
    "覆盖",
    "数据库",
    "密钥",
    "密码",
    "token",
    "secret",
    "ssh",
    "权限",
    "防火墙",
    "端口",
    "重启",
    "kill",
    "安装",
    "卸载",
    "deploy",
    "production",
    "drop",
    "reset",
)
HIGH_RISK_PATH_HINTS = (
    ".env",
    "settings.py",
    "dockerfile",
    "docker-compose",
    "requirements.txt",
    "migrations/",
    "remote_agent.py",
    "remote_executor.py",
    "feishu_views.py",
)


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


def _history_text(history):
    if not history:
        return "(no prior conversation)"

    lines = []
    for item in list(history)[-8:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = re.sub(r"\s+", " ", (item.get("content") or "").strip())
        if role not in {"user", "assistant"} or not content:
            continue
        if len(content) > 280:
            content = content[:277] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no prior conversation)"


def _merge_notes(*groups):
    merged = []
    for group in groups:
        for item in group or []:
            text = (item or "").strip()
            if text and text not in merged:
                merged.append(text)
    return merged


def strip_mode_prefix(user_message):
    text = (user_message or "").strip()
    if CHAT_PREFIX_PATTERN.match(text):
        return INTENT_CHAT, CHAT_PREFIX_PATTERN.sub("", text, count=1).strip()
    if REPO_PREFIX_PATTERN.match(text):
        return INTENT_REPO, REPO_PREFIX_PATTERN.sub("", text, count=1).strip()
    return None, text


def classify_user_request(user_message, history=None):
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

Recent conversation:
{_history_text(history)}

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


def answer_general_question(user_message, history=None):
    _, cleaned_message = strip_mode_prefix(user_message)
    if not cleaned_message:
        raise RemotePlanningError("General question is empty.")

    prompt = cleaned_message
    if history:
        prompt = f"""Recent conversation:
{_history_text(history)}

User message:
{cleaned_message}
"""

    response = create_text_response(
        prompt,
        reasoning_effort="low",
        verbosity="medium",
        instructions=(
            "You are linuxclaw, a Feishu AI assistant. "
            "Reply in concise Chinese by default. "
            "Answer directly without saying you will inspect the repository unless the user explicitly asks for project or server work. "
            "Use the recent conversation only when it is relevant. "
            "If the user asks what you can do, explain that normal Q&A is answered directly, "
            "read-only server checks are executed directly, low-risk repository changes can be applied directly after inspection, "
            "and high-risk changes require confirmation."
        ),
    )
    return response["text"]


def _looks_like_action_request(text):
    return any(hint in text for hint in ACTION_HINTS)


def _trim_command_output(text, limit=900):
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _extract_directory_path(text):
    match = ABSOLUTE_PATH_PATTERN.search(text or "")
    if not match:
        return None
    return match.group(1)


def _build_inspection_steps(user_message):
    text = strip_mode_prefix(user_message)[1].lower()
    if not _looks_like_action_request(text):
        return None

    path = _extract_directory_path(text)
    if path and ("占用" in text or any(hint in text for hint in DIRECTORY_HINTS)):
        return {
            "summary": f"已直接查看目录占用: {path}",
            "commands": [
                ("目录占用", f"du -sh {shlex.quote(path)}"),
            ],
        }

    if any(hint in text for hint in DISK_HINTS):
        return {
            "summary": "已直接查看服务器磁盘与存储信息。",
            "commands": [
                ("磁盘总览", "df -h"),
                ("磁盘与分区", "lsblk"),
            ],
        }

    if any(hint in text for hint in MEMORY_HINTS):
        return {
            "summary": "已直接查看服务器内存信息。",
            "commands": [
                ("内存总览", "free -h"),
                ("系统负载", "uptime"),
            ],
        }

    if any(hint in text for hint in CPU_HINTS):
        return {
            "summary": "已直接查看服务器 CPU 与负载信息。",
            "commands": [
                ("系统负载", "uptime"),
                ("CPU 核心数", "nproc"),
                ("CPU 占用 Top", "ps aux --sort=-%cpu | head -n 10"),
            ],
        }

    if any(hint in text for hint in DOCKER_HINTS):
        return {
            "summary": "已直接查看 Docker 容器状态。",
            "commands": [
                ("容器列表", "docker ps"),
                ("容器资源", "docker stats --no-stream"),
            ],
        }

    if any(hint in text for hint in PORT_HINTS):
        return {
            "summary": "已直接查看监听端口信息。",
            "commands": [
                ("监听端口", "ss -lntp"),
            ],
        }

    if any(hint in text for hint in PROCESS_HINTS):
        return {
            "summary": "已直接查看进程资源占用。",
            "commands": [
                ("内存占用 Top", "ps aux --sort=-%mem | head -n 10"),
                ("CPU 占用 Top", "ps aux --sort=-%cpu | head -n 10"),
            ],
        }

    return None


def answer_read_only_request(executor, user_message):
    inspection = _build_inspection_steps(user_message)
    if not inspection:
        return None

    lines = [inspection["summary"]]
    for label, command in inspection["commands"]:
        output = executor.run_read_only_command(command)
        lines.append(f"{label}:")
        lines.append(_trim_command_output(output))
    return "\n".join(lines)


def select_files_for_request(user_message, repo_files, git_status, history=None):
    repo_file_set = set(repo_files)
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

Recent conversation:
{_history_text(history)}

User request:
{user_message}

Git status:
{git_status or "(clean)"}

Repo index:
{_repo_index_text(repo_files)}
"""
    payload = _call_json_model(prompt)
    files_to_read = [
        path for path in payload.get("files_to_read", []) if path in repo_file_set
    ][:6]
    return {
        "mode": payload.get("mode") or "inspect",
        "reply": payload.get("reply", "").strip(),
        "files_to_read": files_to_read,
        "notes": payload.get("notes", []),
    }


def draft_change_plan(user_message, git_status, inspected_files, history=None):
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

Recent conversation:
{_history_text(history)}

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


def build_change_request_plan(executor, user_message, history=None):
    repo_files = executor.list_files()
    git_status = executor.git_status()
    repo_file_set = set(repo_files)
    selection = select_files_for_request(user_message, repo_files, git_status, history=history)
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

        draft = draft_change_plan(user_message, git_status, inspected_files, history=history)
        additional = [
            path
            for path in draft["additional_files_to_read"]
            if path in repo_file_set and path not in inspected_files
        ][:4]
        if draft["edits"] or not additional:
            draft["inspected_files"] = list(inspected_files.keys())
            draft["git_status"] = git_status
            return draft
        pending_paths = additional

    raise RemotePlanningError("Could not produce a stable change plan.")


def assess_plan_risk(user_message, plan):
    edits = plan.get("edits", [])
    reasons = []
    touched_paths = []
    lowered_message = (user_message or "").lower()

    for edit in edits:
        edit_type = (edit.get("type") or "").strip().lower()
        path = (edit.get("path") or "").strip()
        lowered_path = path.lower()

        if path and path not in touched_paths:
            touched_paths.append(path)

        if edit_type == "write_file":
            reasons.append(f"包含整文件写入: {path or 'unknown file'}")

        if lowered_path and any(hint in lowered_path for hint in HIGH_RISK_PATH_HINTS):
            reasons.append(f"涉及关键文件: {path}")

    if len(touched_paths) > 3:
        reasons.append("涉及文件超过 3 个")

    if any(keyword in lowered_message for keyword in HIGH_RISK_KEYWORDS):
        reasons.append("需求包含高风险操作关键词")

    return {
        "level": "high" if reasons else "low",
        "requires_confirmation": bool(reasons),
        "reasons": _merge_notes(reasons, plan.get("risks", [])),
    }


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
