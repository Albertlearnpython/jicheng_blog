import json
import shlex
import socket
from dataclasses import dataclass

import paramiko
from django.conf import settings


class CodexError(Exception):
    pass


class CodexConfigError(CodexError):
    pass


class CodexExecutionError(CodexError):
    pass


@dataclass
class CodexTurnResult:
    thread_id: str
    reply_text: str
    raw_stdout: str = ""
    raw_stderr: str = ""


class CodexSSHClient:
    _RETRYABLE_RESUME_MARKERS = ("not found", "no matching", "unknown", "missing")

    def run_turn(self, user_message, thread_id="", sandbox="", workdir=""):
        prompt = self._build_prompt(
            user_message,
            sandbox=sandbox,
            workdir=workdir,
        )

        try:
            return self._run_remote_turn(
                prompt,
                thread_id=thread_id,
                sandbox=sandbox,
                workdir=workdir,
            )
        except CodexExecutionError as exc:
            if thread_id and self._should_retry_without_thread(exc):
                return self._run_remote_turn(
                    prompt,
                    thread_id="",
                    sandbox=sandbox,
                    workdir=workdir,
                )
            raise

    def _run_remote_turn(self, prompt, thread_id="", sandbox="", workdir=""):
        client = self._connect()
        command = self._build_command(
            thread_id=thread_id,
            sandbox=sandbox,
            workdir=workdir,
        )

        try:
            stdin, stdout, stderr = client.exec_command(
                command,
                timeout=settings.CODEX_TIMEOUT_SECONDS,
            )
            stdin.channel.sendall(prompt.encode("utf-8"))
            stdin.channel.shutdown_write()

            exit_status = stdout.channel.recv_exit_status()
            stdout_text = stdout.read().decode("utf-8", "replace")
            stderr_text = stderr.read().decode("utf-8", "replace")
        except (socket.timeout, TimeoutError) as exc:
            raise CodexExecutionError(
                f"Codex timed out after {settings.CODEX_TIMEOUT_SECONDS} seconds."
            ) from exc
        except paramiko.SSHException as exc:
            raise CodexExecutionError(f"SSH error while running Codex: {exc}") from exc
        finally:
            client.close()

        if exit_status != 0:
            detail = self._clean_error_detail(stderr_text or stdout_text)
            raise CodexExecutionError(detail or "Codex exited with a non-zero status.")

        return self._parse_turn_result(stdout_text, stderr_text)

    def _connect(self):
        if not settings.CODEX_SSH_HOST:
            raise CodexConfigError("CODEX_SSH_HOST is not configured.")
        if not settings.CODEX_SSH_USER:
            raise CodexConfigError("CODEX_SSH_USER is not configured.")
        if not settings.CODEX_SSH_PASSWORD and not settings.CODEX_SSH_IDENTITY_FILE:
            raise CodexConfigError("Configure CODEX_SSH_PASSWORD or CODEX_SSH_IDENTITY_FILE.")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": settings.CODEX_SSH_HOST,
            "port": settings.CODEX_SSH_PORT,
            "username": settings.CODEX_SSH_USER,
            "timeout": settings.CODEX_SSH_CONNECT_TIMEOUT,
            "auth_timeout": settings.CODEX_SSH_CONNECT_TIMEOUT,
            "banner_timeout": settings.CODEX_SSH_CONNECT_TIMEOUT,
        }
        if settings.CODEX_SSH_PASSWORD:
            connect_kwargs["password"] = settings.CODEX_SSH_PASSWORD
        if settings.CODEX_SSH_IDENTITY_FILE:
            connect_kwargs["key_filename"] = settings.CODEX_SSH_IDENTITY_FILE

        try:
            client.connect(**connect_kwargs)
        except Exception as exc:
            raise CodexExecutionError(f"SSH connection failed: {exc}") from exc

        return client

    def _build_command(self, thread_id="", sandbox="", workdir=""):
        sandbox = (sandbox or settings.CODEX_SANDBOX).strip() or settings.CODEX_SANDBOX
        workdir = (workdir or settings.CODEX_WORKDIR).strip() or settings.CODEX_WORKDIR

        if thread_id:
            command = [
                settings.CODEX_BIN,
                "exec",
                "resume",
                "--json",
                "--skip-git-repo-check",
                "--model",
                settings.CODEX_MODEL,
                "-c",
                f'model_reasoning_effort="{settings.CODEX_REASONING_EFFORT}"',
            ]
        else:
            command = [settings.CODEX_BIN, "exec"]
            if settings.CODEX_PROFILE:
                command.extend(["--profile", settings.CODEX_PROFILE])
            command.extend(
                [
                    "--json",
                    "--skip-git-repo-check",
                    "--sandbox",
                    sandbox,
                    "--cd",
                    workdir,
                    "--model",
                    settings.CODEX_MODEL,
                    "-c",
                    f'model_reasoning_effort="{settings.CODEX_REASONING_EFFORT}"',
                ]
            )
        if settings.CODEX_DISABLE_RESPONSE_STORAGE:
            command.extend(["-c", "disable_response_storage=true"])

        if thread_id:
            command.extend([thread_id, "-"])
        else:
            command.append("-")

        return " ".join(shlex.quote(part) for part in command)

    def _build_prompt(self, user_message, sandbox="", workdir=""):
        cleaned = (user_message or "").strip()
        if not cleaned:
            raise CodexExecutionError("Incoming Feishu message is empty.")

        sandbox = (sandbox or settings.CODEX_SANDBOX).strip() or settings.CODEX_SANDBOX
        workdir = (workdir or settings.CODEX_WORKDIR).strip() or settings.CODEX_WORKDIR
        is_privileged = sandbox == "danger-full-access"

        capability_lines = [
            f"Current sandbox mode: {sandbox}.",
            f"Primary working directory: {workdir}.",
        ]
        if is_privileged:
            capability_lines.extend(
                [
                    "This session is running on the user's Linux host with direct file and service access.",
                    "If the user asks you to create, edit, or inspect files, run commands, or manage services, do it directly in this Codex session when feasible.",
                    "Do not say you lack permission or local machine access unless a command actually fails.",
                ]
            )
        else:
            capability_lines.extend(
                [
                    "This session is intentionally restricted.",
                    "You may inspect and analyze, but do not claim to have performed writes or host-level changes unless you actually did.",
                ]
            )

        return (
            "You are replying to a user through a Feishu bot.\n"
            "Reply in concise Chinese by default.\n"
            "Keep Markdown lightweight and readable in chat.\n"
            "Use first-principles reasoning and avoid cargo-culting or blind path dependence.\n"
            "Do not assume the user fully understands the real goal.\n"
            "Start from the original requirement and problem.\n"
            "If the goal is ambiguous, stop and ask for clarification before taking irreversible actions.\n"
            "If the goal is clear but the path is not optimal, directly propose the shorter and lower-cost option.\n"
            "Every reply must contain exactly these two sections:\n"
            "1. Direct execution: follow the user's current request and provide the concrete task result.\n"
            "2. Deep interaction: carefully challenge the original need, including possible XY-problem drift, drawbacks in the current path, and a more elegant alternative when appropriate.\n"
            "Render those two section headings in Chinese as: `直接执行` and `深度交互`.\n"
            "Do not claim you ran commands or changed files unless you actually did so in this Codex session.\n"
            f"{' '.join(capability_lines)}\n\n"
            f"User message:\n{cleaned}\n"
        )

    def _parse_turn_result(self, stdout_text, stderr_text):
        thread_id = ""
        reply_text = ""

        for raw_line in stdout_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            if payload.get("type") == "thread.started":
                thread_id = (payload.get("thread_id") or "").strip()
                continue

            if payload.get("type") != "item.completed":
                continue

            item = payload.get("item") or {}
            if item.get("type") != "agent_message":
                continue

            text = (item.get("text") or "").strip()
            if not text:
                text = self._extract_content_text(item)
            if text:
                reply_text = text

        if not thread_id:
            detail = self._clean_error_detail(stderr_text or stdout_text)
            raise CodexExecutionError(detail or "Codex did not return a thread id.")

        if not reply_text:
            raise CodexExecutionError("Codex did not return a reply message.")

        return CodexTurnResult(
            thread_id=thread_id,
            reply_text=reply_text[: settings.CODEX_MAX_OUTPUT_CHARS].strip(),
            raw_stdout=stdout_text,
            raw_stderr=stderr_text,
        )

    def _extract_content_text(self, item):
        chunks = []
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "output_text":
                continue
            text = (block.get("text") or "").strip()
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    def _should_retry_without_thread(self, exc):
        text = str(exc).lower()
        mentions_session = "session" in text or "thread" in text
        return mentions_session and any(marker in text for marker in self._RETRYABLE_RESUME_MARKERS)

    def _clean_error_detail(self, text):
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return ""
        return " ".join(lines[:4])
