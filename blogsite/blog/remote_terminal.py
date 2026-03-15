import hashlib
import json

from django.conf import settings

from .remote_executor import RemoteExecutor, RemoteExecutorError


class RemoteTerminalError(RemoteExecutorError):
    pass


class RemoteTerminalManager(RemoteExecutor):
    KEY_MAP = {
        "ctrl-c": "C-c",
        "enter": "Enter",
        "esc": "Escape",
        "escape": "Escape",
        "tab": "Tab",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "backspace": "BSpace",
        "delete": "DC",
    }

    PROFILE_MAP = {
        "shell": {
            "command": None,
            "required_binary": "",
        },
        "codex": {
            "command": "exec codex",
            "required_binary": "codex",
        },
    }

    def __init__(self):
        super().__init__()
        self.capture_lines = int(getattr(settings, "REMOTE_TERMINAL_CAPTURE_LINES", 80))
        self.wait_seconds = float(getattr(settings, "REMOTE_TERMINAL_WAIT_SECONDS", 0.6))
        self.max_input_length = int(getattr(settings, "REMOTE_TERMINAL_MAX_INPUT", 2000))
        self.session_prefix = getattr(settings, "REMOTE_TERMINAL_SESSION_PREFIX", "linuxclaw")

        shell_command = getattr(settings, "REMOTE_TERMINAL_SHELL_COMMAND", "").strip()
        codex_command = getattr(settings, "REMOTE_TERMINAL_CODEX_COMMAND", "").strip()
        if shell_command:
            self.PROFILE_MAP["shell"]["command"] = shell_command
        if codex_command:
            self.PROFILE_MAP["codex"]["command"] = codex_command

    def build_session_name(self, chat_id):
        digest = hashlib.sha1((chat_id or "").encode("utf-8")).hexdigest()[:16]
        prefix = "".join(ch for ch in self.session_prefix.lower() if ch.isalnum()) or "linuxclaw"
        return f"{prefix}-{digest}"

    def resolve_cwd(self, path_value=None):
        candidate = (path_value or "").strip()
        if not candidate:
            return self.root_path.as_posix()
        return self._resolve_path(candidate).as_posix()

    def profile_config(self, profile):
        normalized = (profile or "shell").strip().lower()
        if normalized not in self.PROFILE_MAP:
            raise RemoteTerminalError(f"Unsupported terminal profile: {profile}")
        config = dict(self.PROFILE_MAP[normalized])
        config["profile"] = normalized
        return config

    def _run_terminal_action(self, action, **payload):
        request_payload = dict(payload)
        request_payload["action"] = action
        script = f"""
import json
import subprocess
import sys
import time

payload = json.loads({json.dumps(json.dumps(request_payload))})

def run(command):
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )

def output(data):
    print(json.dumps(data, ensure_ascii=False))

def fail(message, error_code="terminal_error"):
    output({{"ok": False, "error": message, "error_code": error_code}})
    sys.exit(0)

def ensure_tmux():
    result = run(["bash", "-lc", "command -v tmux >/dev/null 2>&1"])
    if result.returncode != 0:
        fail("tmux is not installed on the remote server.", "tmux_missing")

def session_exists(name):
    return run(["tmux", "has-session", "-t", name]).returncode == 0

def session_capture(name, lines):
    result = run(["tmux", "capture-pane", "-p", "-J", "-t", f"{{name}}:0", "-S", f"-{{lines}}"])
    if result.returncode != 0:
        fail((result.stderr or result.stdout).strip() or "Failed to capture tmux pane.")
    return result.stdout.strip()

def pane_value(name, fmt):
    result = run(["tmux", "display-message", "-p", "-t", f"{{name}}:0", fmt])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()

def snapshot(name, lines):
    return {{
        "session_name": name,
        "exists": True,
        "cwd": pane_value(name, "#{{pane_current_path}}"),
        "program": pane_value(name, "#{{pane_current_command}}"),
        "output": session_capture(name, lines),
    }}

ensure_tmux()
action = payload.get("action")
name = payload.get("session_name") or ""
lines = int(payload.get("capture_lines") or 80)

if action == "status":
    if not session_exists(name):
        output({{"ok": True, "exists": False, "session_name": name}})
    else:
        output({{"ok": True, **snapshot(name, lines)}})
    sys.exit(0)

if action == "open":
    cwd = payload.get("cwd") or ""
    required_binary = (payload.get("required_binary") or "").strip()
    command = payload.get("command")
    if required_binary:
        result = run(["bash", "-lc", f"command -v {{required_binary}} >/dev/null 2>&1"])
        if result.returncode != 0:
            fail(f"Required command is not installed: {{required_binary}}", "binary_missing")
    if session_exists(name):
        output({{"ok": True, "created": False, **snapshot(name, lines)}})
        sys.exit(0)
    command_args = ["tmux", "new-session", "-d", "-s", name, "-c", cwd]
    if command:
        command_args.append(command)
    result = run(command_args)
    if result.returncode != 0:
        fail((result.stderr or result.stdout).strip() or "Failed to create tmux session.")
    time.sleep(float(payload.get("wait_seconds") or 0.4))
    output({{"ok": True, "created": True, **snapshot(name, lines)}})
    sys.exit(0)

if not session_exists(name):
    fail("Remote terminal session is not active.", "missing_session")

if action == "send":
    text = payload.get("text") or ""
    enter = bool(payload.get("enter", True))
    lines_to_send = text.splitlines() or [""]
    for item in lines_to_send:
        result = run(["tmux", "send-keys", "-t", f"{{name}}:0", "-l", item])
        if result.returncode != 0:
            fail((result.stderr or result.stdout).strip() or "Failed to send input to tmux.")
        if enter:
            result = run(["tmux", "send-keys", "-t", f"{{name}}:0", "Enter"])
            if result.returncode != 0:
                fail((result.stderr or result.stdout).strip() or "Failed to send Enter to tmux.")
    time.sleep(float(payload.get("wait_seconds") or 0.4))
    output({{"ok": True, **snapshot(name, lines)}})
    sys.exit(0)

if action == "key":
    key_name = payload.get("key_name") or ""
    result = run(["tmux", "send-keys", "-t", f"{{name}}:0", key_name])
    if result.returncode != 0:
        fail((result.stderr or result.stdout).strip() or "Failed to send key to tmux.")
    time.sleep(float(payload.get("wait_seconds") or 0.4))
    output({{"ok": True, **snapshot(name, lines)}})
    sys.exit(0)

if action == "close":
    result = run(["tmux", "kill-session", "-t", name])
    if result.returncode != 0:
        fail((result.stderr or result.stdout).strip() or "Failed to close tmux session.")
    output({{"ok": True, "closed": True, "session_name": name}})
    sys.exit(0)

fail(f"Unsupported terminal action: {{action}}")
"""
        payload = json.loads(self._run_remote_python(script, timeout=self.command_timeout))
        if not payload.get("ok"):
            raise RemoteTerminalError(payload.get("error") or "Remote terminal operation failed.")
        return payload

    def status(self, chat_id):
        return self._run_terminal_action(
            "status",
            session_name=self.build_session_name(chat_id),
            capture_lines=self.capture_lines,
        )

    def open(self, chat_id, profile="shell", cwd=None):
        profile_config = self.profile_config(profile)
        return self._run_terminal_action(
            "open",
            session_name=self.build_session_name(chat_id),
            command=profile_config["command"],
            required_binary=profile_config["required_binary"],
            cwd=self.resolve_cwd(cwd),
            capture_lines=self.capture_lines,
            wait_seconds=self.wait_seconds,
        )

    def read(self, chat_id):
        snapshot = self.status(chat_id)
        if not snapshot.get("exists"):
            raise RemoteTerminalError("Remote terminal session is not active.")
        return snapshot

    def send(self, chat_id, text, enter=True):
        candidate = (text or "").rstrip("\n")
        if len(candidate) > self.max_input_length:
            raise RemoteTerminalError(
                f"Terminal input is too long ({len(candidate)} > {self.max_input_length})."
            )
        return self._run_terminal_action(
            "send",
            session_name=self.build_session_name(chat_id),
            text=candidate,
            enter=bool(enter),
            capture_lines=self.capture_lines,
            wait_seconds=self.wait_seconds,
        )

    def send_key(self, chat_id, key):
        normalized = (key or "").strip().lower()
        if normalized not in self.KEY_MAP:
            raise RemoteTerminalError(f"Unsupported terminal key: {key}")
        return self._run_terminal_action(
            "key",
            session_name=self.build_session_name(chat_id),
            key_name=self.KEY_MAP[normalized],
            capture_lines=self.capture_lines,
            wait_seconds=self.wait_seconds,
        )

    def close(self, chat_id):
        return self._run_terminal_action(
            "close",
            session_name=self.build_session_name(chat_id),
        )
