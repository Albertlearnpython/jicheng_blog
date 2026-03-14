import base64
import json
import posixpath
from contextlib import contextmanager
from pathlib import PurePosixPath

import paramiko
from django.conf import settings


class RemoteExecutorError(Exception):
    pass


class RemoteExecutorConfigError(RemoteExecutorError):
    pass


def _normalize_posix_path(path_value):
    path = PurePosixPath(path_value)
    parts = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return PurePosixPath("/" + "/".join(parts))


class RemoteExecutor:
    def __init__(self):
        self.host = settings.REMOTE_SSH_HOST
        self.port = settings.REMOTE_SSH_PORT
        self.user = settings.REMOTE_SSH_USER
        self.password = settings.REMOTE_SSH_PASSWORD
        self.identity_file = settings.REMOTE_SSH_IDENTITY_FILE
        self.project_root = settings.REMOTE_PROJECT_ROOT
        self.command_timeout = settings.REMOTE_COMMAND_TIMEOUT
        self.read_limit = settings.REMOTE_READ_FILE_LIMIT
        self.list_limit = settings.REMOTE_LIST_FILE_LIMIT
        self.list_depth = settings.REMOTE_LIST_FILE_DEPTH
        self.allowed_test_prefixes = tuple(settings.REMOTE_ALLOWED_TEST_PREFIXES)
        self.allowed_inspection_prefixes = tuple(settings.REMOTE_ALLOWED_INSPECTION_PREFIXES)
        self.require_clean_worktree = settings.REMOTE_REQUIRE_CLEAN_WORKTREE
        self._root_path = None

    def ensure_configured(self):
        missing = []
        if not self.host:
            missing.append("REMOTE_SSH_HOST")
        if not self.user:
            missing.append("REMOTE_SSH_USER")
        if not self.project_root:
            missing.append("REMOTE_PROJECT_ROOT")
        if not self.password and not self.identity_file:
            missing.append("REMOTE_SSH_PASSWORD or REMOTE_SSH_IDENTITY_FILE")
        if missing:
            raise RemoteExecutorConfigError(
                "Remote executor is not configured: " + ", ".join(missing)
            )
        root_path = _normalize_posix_path(self.project_root)
        if not str(root_path).startswith("/"):
            raise RemoteExecutorConfigError("REMOTE_PROJECT_ROOT must be an absolute POSIX path.")
        self._root_path = root_path

    @property
    def root_path(self):
        if self._root_path is None:
            self.ensure_configured()
        return self._root_path

    @contextmanager
    def _connect(self):
        self.ensure_configured()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": self.command_timeout,
            "banner_timeout": self.command_timeout,
            "auth_timeout": self.command_timeout,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if self.password:
            connect_kwargs["password"] = self.password
        if self.identity_file:
            connect_kwargs["key_filename"] = self.identity_file
            connect_kwargs["look_for_keys"] = not self.password

        try:
            client.connect(**connect_kwargs)
            yield client
        except FileNotFoundError as exc:
            raise RemoteExecutorConfigError(
                f"SSH identity file not found: {self.identity_file}"
            ) from exc
        except paramiko.AuthenticationException as exc:
            raise RemoteExecutorError("Remote SSH authentication failed.") from exc
        except paramiko.SSHException as exc:
            raise RemoteExecutorError(f"Remote SSH error: {exc}") from exc
        except OSError as exc:
            raise RemoteExecutorError(f"Remote SSH connection failed: {exc}") from exc
        finally:
            client.close()

    def _run_command(self, command, timeout=None):
        with self._connect() as client:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout or self.command_timeout)
            output = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                detail = (error or output).strip()
                raise RemoteExecutorError(detail or "Remote command failed.")
            return output

    def _run_remote_python(self, script, timeout=None):
        with self._connect() as client:
            stdin, stdout, stderr = client.exec_command("python3 -", timeout=timeout or self.command_timeout)
            stdin.write(script)
            stdin.flush()
            stdin.channel.shutdown_write()
            output = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                detail = (error or output).strip()
                raise RemoteExecutorError(detail or "Remote python command failed.")
            return output

    def _run_bash(self, command, timeout=None):
        return self._run_command(f"bash -lc {json.dumps(command)}", timeout=timeout)

    def _resolve_path(self, path_value):
        candidate = PurePosixPath(path_value)
        if not candidate.is_absolute():
            candidate = self.root_path / candidate
        normalized = _normalize_posix_path(str(candidate))
        try:
            normalized.relative_to(self.root_path)
        except ValueError as exc:
            raise RemoteExecutorError(
                f"Path is outside REMOTE_PROJECT_ROOT: {path_value}"
            ) from exc
        return normalized

    def list_files(self):
        script = f"""
import json
from pathlib import Path

root = Path({self.root_path.as_posix()!r})
limit = {self.list_limit}
max_depth = {self.list_depth}
files = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    relative = path.relative_to(root)
    if any(part in {{".git", "__pycache__", "node_modules", ".venv"}} for part in relative.parts):
        continue
    if len(relative.parts) > max_depth:
        continue
    files.append(relative.as_posix())
files.sort()
print(json.dumps({{"files": files[:limit]}}, ensure_ascii=False))
"""
        payload = json.loads(self._run_remote_python(script))
        return payload["files"]

    def git_status(self):
        root = self.root_path.as_posix()
        return self._run_bash(f"git -C {root} status --short").strip()

    def git_diff(self, paths=None):
        root = self.root_path.as_posix()
        path_args = ""
        if paths:
            resolved = []
            for path in paths:
                relative = self._resolve_path(path).relative_to(self.root_path).as_posix()
                resolved.append(json.dumps(relative))
            path_args = " -- " + " ".join(resolved)
        return self._run_bash(f"git -C {root} diff{path_args}").strip()

    def assert_clean_worktree(self):
        if not self.require_clean_worktree:
            return
        status = self.git_status()
        if status:
            raise RemoteExecutorError(
                "Remote git worktree is not clean. Refusing to apply edits."
            )

    def read_file(self, path_value, limit=None):
        path = self._resolve_path(path_value)
        read_limit = self.read_limit if limit is None else int(limit)
        script = f"""
import json
from pathlib import Path

path = Path({path.as_posix()!r})
limit = {read_limit}
text = path.read_text(encoding="utf-8", errors="replace")
truncated = len(text) > limit
if truncated:
    text = text[:limit]
print(json.dumps({{"content": text, "truncated": truncated}}, ensure_ascii=False))
"""
        payload = json.loads(self._run_remote_python(script))
        return payload

    def read_full_file(self, path_value):
        return self.read_file(path_value, limit=10**9)["content"]

    def path_exists(self, path_value):
        path = self._resolve_path(path_value)
        script = f"""
import json
from pathlib import Path

path = Path({path.as_posix()!r})
print(json.dumps({{"exists": path.exists()}}, ensure_ascii=False))
"""
        payload = json.loads(self._run_remote_python(script))
        return bool(payload["exists"])

    def _sftp_write_text(self, path, content):
        with self._connect() as client:
            sftp = client.open_sftp()
            try:
                parent = posixpath.dirname(path)
                self._ensure_remote_dirs(sftp, parent)
                with sftp.open(path, "w") as remote_file:
                    remote_file.write(content)
            finally:
                sftp.close()

    def _ensure_remote_dirs(self, sftp, path):
        current = path
        stack = []
        while current and current != "/":
            stack.append(current)
            current = posixpath.dirname(current)
        for item in reversed(stack):
            try:
                sftp.stat(item)
            except FileNotFoundError:
                sftp.mkdir(item)
            except OSError:
                try:
                    sftp.stat(item)
                except OSError as exc:
                    raise RemoteExecutorError(f"Unable to create remote directory: {item}") from exc

    def write_file(self, path_value, content):
        path = self._resolve_path(path_value).as_posix()
        self._sftp_write_text(path, content)

    def delete_file(self, path_value):
        path = self._resolve_path(path_value).as_posix()
        with self._connect() as client:
            sftp = client.open_sftp()
            try:
                sftp.remove(path)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise RemoteExecutorError(f"Unable to delete remote file: {path}") from exc
            finally:
                sftp.close()

    def replace_text(self, path_value, search_text, replace_text):
        path = self._resolve_path(path_value)
        text = self.read_full_file(path.as_posix())
        count = text.count(search_text)
        if count != 1:
            raise RemoteExecutorError(
                f"Expected exactly one match in {path.as_posix()}, found {count}."
            )
        self.write_file(path.as_posix(), text.replace(search_text, replace_text, 1))

    def run_test_command(self, command):
        candidate = (command or "").strip()
        if not candidate:
            raise RemoteExecutorError("Test command is empty.")
        if not any(candidate.startswith(prefix) for prefix in self.allowed_test_prefixes):
            raise RemoteExecutorError(f"Test command is not allowed: {candidate}")
        root = self.root_path.as_posix()
        return self._run_bash(f"cd {root} && {candidate}", timeout=self.command_timeout)

    def run_read_only_command(self, command):
        candidate = (command or "").strip()
        if not candidate:
            raise RemoteExecutorError("Inspection command is empty.")
        if not any(candidate.startswith(prefix) for prefix in self.allowed_inspection_prefixes):
            raise RemoteExecutorError(f"Inspection command is not allowed: {candidate}")
        root = self.root_path.as_posix()
        return self._run_bash(f"cd {root} && {candidate}", timeout=self.command_timeout)
