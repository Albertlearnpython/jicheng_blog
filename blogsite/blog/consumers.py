import asyncio
import contextlib

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from .models import FeishuChatSession
from .remote_executor import RemoteExecutorError
from .remote_terminal import RemoteTerminalError, RemoteTerminalManager
from .terminal_state import (
    TerminalSessionError,
    clear_terminal_state,
    get_terminal_state,
    resolve_terminal_session,
    terminal_output_delta,
    terminal_snapshot_payload,
    update_terminal_state,
)


def _build_web_terminal_manager():
    manager = RemoteTerminalManager()
    manager.capture_lines = int(getattr(settings, "TERMINAL_WEB_CAPTURE_LINES", manager.capture_lines))
    return manager


def _terminal_status_snapshot(chat_id):
    return _build_web_terminal_manager().status(chat_id)


def _terminal_open_snapshot(chat_id, profile, cwd):
    return _build_web_terminal_manager().open(chat_id, profile=profile, cwd=cwd)


def _terminal_send_snapshot(chat_id, text):
    return _build_web_terminal_manager().send(chat_id, text, enter=True)


def _terminal_key_snapshot(chat_id, key_name):
    return _build_web_terminal_manager().send_key(chat_id, key_name)


def _terminal_close_session(chat_id):
    return _build_web_terminal_manager().close(chat_id)


def _load_terminal_context(token):
    session, payload = resolve_terminal_session(token)
    state = get_terminal_state(session)
    return {
        "chat_id": session.chat_id,
        "default_profile": payload.get("profile") or state.get("profile") or "shell",
    }


def _store_terminal_snapshot(chat_id, snapshot, fallback_profile):
    session = FeishuChatSession.objects.filter(chat_id=chat_id).first()
    if not session:
        raise TerminalSessionError("Terminal session was not found.")
    return update_terminal_state(
        session,
        active=True,
        profile=snapshot.get("profile") or fallback_profile or "shell",
        passthrough=True,
        cwd=snapshot.get("cwd", ""),
        program=snapshot.get("program", ""),
        output=snapshot.get("output", ""),
    )


def _clear_terminal_snapshot(chat_id):
    session = FeishuChatSession.objects.filter(chat_id=chat_id).first()
    if not session:
        raise TerminalSessionError("Terminal session was not found.")
    return clear_terminal_state(session)


class TerminalConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.poll_task = None
        self.chat_id = ""
        self.default_profile = "shell"
        self.last_output = ""
        self.last_program = ""
        self.last_cwd = ""
        self.last_active = None
        self.last_profile = ""

        token = self.scope["url_route"]["kwargs"]["token"]
        try:
            context = await database_sync_to_async(_load_terminal_context)(token)
        except TerminalSessionError:
            await self.close(code=4404)
            return

        self.chat_id = context["chat_id"]
        self.default_profile = context["default_profile"]
        self.last_profile = self.default_profile

        await self.accept()
        await self._push_status_snapshot(force_replace=True)
        self.poll_task = asyncio.create_task(self._poll_terminal())

    async def disconnect(self, close_code):
        if self.poll_task:
            self.poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.poll_task

    async def receive_json(self, content, **kwargs):
        action = (content.get("action") or "").strip().lower()
        if action == "open":
            profile = (content.get("profile") or self.default_profile or "shell").strip().lower()
            cwd = (content.get("cwd") or "").strip()
            await self._run_action(
                lambda: _terminal_open_snapshot(self.chat_id, profile, cwd),
                profile=profile,
                force_replace=True,
            )
            return

        if action == "send":
            text = (content.get("text") or "").strip()
            if not text:
                await self.send_json({"ok": False, "event": "error", "error": "Enter a command first."})
                return
            await self._run_action(lambda: _terminal_send_snapshot(self.chat_id, text))
            return

        if action == "key":
            key_name = (content.get("key") or "").strip().lower()
            if not key_name:
                await self.send_json({"ok": False, "event": "error", "error": "Missing terminal key."})
                return
            await self._run_action(lambda: _terminal_key_snapshot(self.chat_id, key_name))
            return

        if action == "close":
            await self._close_terminal()
            return

        if action == "ping":
            await self.send_json({"ok": True, "event": "pong"})
            return

        await self.send_json({"ok": False, "event": "error", "error": "Unsupported terminal action."})

    async def _poll_terminal(self):
        interval = max(float(getattr(settings, "TERMINAL_WEBSOCKET_POLL_SECONDS", 0.35)), 0.2)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._push_status_snapshot(force_replace=False)
        except asyncio.CancelledError:
            raise

    async def _run_action(self, action, profile=None, force_replace=False):
        try:
            snapshot = await sync_to_async(action, thread_sensitive=False)()
        except (RemoteTerminalError, RemoteExecutorError, TerminalSessionError) as exc:
            await self.send_json({"ok": False, "event": "error", "error": str(exc)})
            return

        if profile:
            snapshot["profile"] = profile
            self.default_profile = profile

        await self._push_snapshot(snapshot, force_replace=force_replace)

    async def _push_status_snapshot(self, force_replace=False):
        try:
            snapshot = await sync_to_async(_terminal_status_snapshot, thread_sensitive=False)(self.chat_id)
        except (RemoteTerminalError, RemoteExecutorError) as exc:
            await self.send_json({"ok": False, "event": "error", "error": str(exc)})
            return

        if not snapshot.get("exists"):
            if self.last_active is False and not force_replace:
                return
            await self._send_closed_snapshot(force_replace=force_replace)
            return

        await self._push_snapshot(snapshot, force_replace=force_replace)

    async def _push_snapshot(self, snapshot, force_replace=False):
        snapshot_profile = snapshot.get("profile") or self.default_profile or "shell"
        snapshot["profile"] = snapshot_profile

        try:
            state = await database_sync_to_async(_store_terminal_snapshot)(
                self.chat_id,
                snapshot,
                snapshot_profile,
            )
        except TerminalSessionError as exc:
            await self.send_json({"ok": False, "event": "error", "error": str(exc)})
            return

        full_output = snapshot.get("output") or ""
        active = bool(snapshot.get("exists", True))
        output_changed = full_output != self.last_output
        meta_changed = any(
            [
                active != self.last_active,
                snapshot.get("program", "") != self.last_program,
                snapshot.get("cwd", "") != self.last_cwd,
                snapshot_profile != self.last_profile,
            ]
        )

        if not force_replace and not output_changed and not meta_changed:
            return

        replace = bool(force_replace or self.last_active is None or not self.last_output)
        payload_output = ""
        if output_changed:
            if replace:
                payload_output = full_output
            else:
                payload_output = terminal_output_delta(self.last_output, full_output)
                if payload_output == full_output:
                    replace = True

        payload = terminal_snapshot_payload(
            state,
            snapshot,
            fallback_profile=snapshot_profile,
            replace=replace,
            event="snapshot",
        )
        payload["output"] = full_output if replace else payload_output
        await self.send_json(payload)

        self.last_output = full_output
        self.last_active = active
        self.last_program = snapshot.get("program", "")
        self.last_cwd = snapshot.get("cwd", "")
        self.last_profile = snapshot_profile

    async def _send_closed_snapshot(self, force_replace=False):
        try:
            state = await database_sync_to_async(_clear_terminal_snapshot)(self.chat_id)
        except TerminalSessionError as exc:
            await self.send_json({"ok": False, "event": "error", "error": str(exc)})
            return

        payload = terminal_snapshot_payload(
            state,
            {
                "exists": False,
                "profile": self.default_profile,
                "cwd": "",
                "program": "",
                "output": "",
            },
            fallback_profile=self.default_profile,
            replace=force_replace,
            event="snapshot",
        )
        await self.send_json(payload)
        self.last_output = ""
        self.last_active = False
        self.last_program = ""
        self.last_cwd = ""
        self.last_profile = self.default_profile

    async def _close_terminal(self):
        try:
            await sync_to_async(_terminal_close_session, thread_sensitive=False)(self.chat_id)
        except (RemoteTerminalError, RemoteExecutorError) as exc:
            await self.send_json({"ok": False, "event": "error", "error": str(exc)})
            return

        await self._send_closed_snapshot(force_replace=False)
