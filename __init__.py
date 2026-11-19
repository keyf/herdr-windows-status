"""Reflect a gateway-backed Hermes TUI turn to Herdr's OSC 7501 status protocol.

The interactive CLI is a gateway client: model hooks run in the long-lived gateway,
whose CONOUT$ is not the terminal tab Herdr watches. The client-side GatewayAgent
is the point that knows a submitted turn has started, reached a blocking request,
or settled. Patch only that local adapter and write OSC from the client process
that owns the Herdr tab.
"""
from __future__ import annotations

import base64
import os
import re
import unicodedata
from typing import Any

LOG_PATH = os.path.join(os.path.dirname(__file__), "debug.log")
_PATCH_MARKER = "_herdr_desktop_status_bridge_installed"
_APP = "hermes"
_MESSAGE_LIMIT = 120
# Herdr's status channel is observable terminal UI, not a secret transport.
# Keep status semantic only; never export model output, commands, or prompts.
_INCLUDE_CONTEXT = False
_TERMINAL_ESCAPE = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|(?:\x1b\]|\x9d|\x1b[P^_]|[\x90\x98\x9e\x9f])[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)"
    r"|\x1b[ -/]*[@-Z\\-_]"
)


def log(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


def _message_text(text: Any) -> str | None:
    """Return one bounded terminal-safe summary without control bytes."""
    if not isinstance(text, str):
        return None
    stripped = _TERMINAL_ESCAPE.sub("", text)
    line = next((part for part in stripped.splitlines() if part.strip()), "")
    line = "".join(
        " " if char.isspace() else char
        for char in line
        if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )
    return " ".join(line.split())[:_MESSAGE_LIMIT] or None


def _request_details(frame: Any) -> tuple[str | None, str | None]:
    """Classify a client-side blocking RPC prompt without relying on one schema."""
    if not isinstance(frame, dict):
        return None, None
    method = str(frame.get("method") or "").lower()
    payload = frame.get("params") or frame.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if method == "clarify":
        questions = payload.get("questions") or []
        first = next(
            (row.get("question") for row in questions if isinstance(row, dict) and row.get("question")),
            None,
        )
        return "question", _message_text(first) or "Waiting for an answer"
    if method in {"sudo", "secret", "vault.unlock_prompt", "vault.save_login", "vault.code"}:
        return "auth", "Waiting to sign in"
    return None, None


def _finish_from_result(result: Any) -> tuple[str, str | None, str | None]:
    """Map one settled client result to one terminal-visible outcome."""
    if not isinstance(result, dict):
        return "done", None, None
    if result.get("interrupted") or result.get("admitted") is False:
        return "idle", None, None
    if result.get("failed"):
        return "error", None, _message_text(result.get("error"))
    reply = result.get("pre_transform_response") if result.get("response_transformed") else None
    if not isinstance(reply, str):
        reply = result.get("final_response")
    return "done", None, _message_text(reply)


def emit_status(state: str, kind: str | None = None, msg: str | None = None) -> None:
    """Write a structured OSC 7501 record to the active Windows console."""
    if state not in {"idle", "working", "blocked", "done", "error", "clear"}:
        log(f"ignored invalid state: {state!r}")
        return
    parts = [f"state={state}", f"app={_APP}"]
    if state == "blocked" and kind in {"permission", "question", "auth"}:
        parts.append(f"kind={kind}")
    if _INCLUDE_CONTEXT and msg:
        encoded = base64.b64encode(msg.encode("utf-8")).decode("ascii")
        if len(encoded) <= 2732:
            parts.append(f"msg={encoded}")
    sequence = "\x1b]7501;" + ":".join(parts) + "\x07"
    log(f"emit: {state}; kind={kind}; pid={os.getpid()}")
    try:
        with open("CONOUT$", "w", encoding="utf-8") as console:
            console.write(sequence)
            console.flush()
    except Exception as exc:
        # The gateway/service has no interactive console; this is expected there.
        log(f"CONOUT$ error: {exc}")


def _install_modal_bridge() -> bool:
    """Report client-native clarify and sudo modals that bypass GatewayAgent RPC."""
    try:
        from hermes_cli.cli_modal_mixin import CLIModalMixin
    except Exception as exc:
        log(f"CLIModalMixin unavailable: {exc}")
        return False
    marker = "_herdr_desktop_status_modal_bridge_installed"
    if getattr(CLIModalMixin, marker, False):
        return True

    original_clarify = getattr(CLIModalMixin, "_clarify_callback", None)
    if callable(original_clarify):
        def clarify_with_herdr_status(self: Any, questions: Any, *args: Any, **kwargs: Any) -> Any:
            first = next(
                (row.get("question") for row in questions or () if isinstance(row, dict) and row.get("question")),
                None,
            )
            emit_status("blocked", "question")
            try:
                return original_clarify(self, questions, *args, **kwargs)
            finally:
                emit_status("working")

        CLIModalMixin._clarify_callback = clarify_with_herdr_status

    original_sudo = getattr(CLIModalMixin, "_sudo_password_callback", None)
    if callable(original_sudo):
        def sudo_with_herdr_status(self: Any, *args: Any, **kwargs: Any) -> Any:
            emit_status("blocked", "auth")
            try:
                return original_sudo(self, *args, **kwargs)
            finally:
                emit_status("working")

        CLIModalMixin._sudo_password_callback = sudo_with_herdr_status

    setattr(CLIModalMixin, marker, True)
    log("installed CLI modal bridge")
    return True


def _install_gateway_client_bridge() -> bool:
    """Patch the local gateway client once; return False outside an interactive client."""
    try:
        from hermes_cli import gateway_agent as gateway_agent_module
        GatewayAgent = gateway_agent_module.GatewayAgent
    except Exception as exc:
        log(f"GatewayAgent unavailable: {exc}")
        return False

    if getattr(GatewayAgent, _PATCH_MARKER, False):
        return True

    original_run = GatewayAgent.run_conversation
    original_answer_request = GatewayAgent._answer_request
    original_on_approval = GatewayAgent._on_approval

    def run_conversation_with_herdr_status(self: Any, *args: Any, **kwargs: Any) -> Any:
        # This runs before prompt.submit, so Herdr changes state immediately when Enter is pressed.
        emit_status("working")
        try:
            result = original_run(self, *args, **kwargs)
        except Exception:
            emit_status("error")
            raise
        state, kind, _message = _finish_from_result(result)
        emit_status(state, kind)
        return result

    def answer_request_with_herdr_status(self: Any, frame: dict[str, Any]) -> Any:
        # The call blocks while the human answers approval / clarify / credential prompts.
        kind, _message = _request_details(frame)
        emit_status("blocked", kind)
        try:
            return original_answer_request(self, frame)
        finally:
            # The turn resumes after the reply is sent; its enclosing run() later settles it.
            emit_status("working")

    def on_approval_with_herdr_status(self: Any, *args: Any, **kwargs: Any) -> Any:
        # Dangerous-command consent is an ``approval.request`` event, not a server request.
        payload = args[0] if args else kwargs.get("payload")
        details = payload if isinstance(payload, dict) else {}
        emit_status("blocked", "permission")
        try:
            return original_on_approval(self, *args, **kwargs)
        finally:
            emit_status("working")


    GatewayAgent.run_conversation = run_conversation_with_herdr_status
    GatewayAgent._answer_request = answer_request_with_herdr_status
    GatewayAgent._on_approval = on_approval_with_herdr_status
    # gateway_agent._HANDLERS is built at module import time and retains the
    # original method object. Rebind its live approval route as well; changing
    # only the class attribute leaves real approval.request events invisible.
    handlers = getattr(gateway_agent_module, "_HANDLERS", None)
    if isinstance(handlers, dict):
        if handlers.get("approval.request") is original_on_approval:
            handlers["approval.request"] = on_approval_with_herdr_status
        else:
            log("approval handler table was not patched: unexpected binding")
    else:
        log("approval handler table unavailable")
    setattr(GatewayAgent, _PATCH_MARKER, True)
    log("installed GatewayAgent client bridge")
    return True


def register(ctx: Any) -> None:
    installed = _install_gateway_client_bridge()
    modals_installed = _install_modal_bridge()
    log(f"plugin registered; gateway client bridge={installed}; modal bridge={modals_installed}")
    # A finite CLI or terminal shutdown unloads its local plugin manager. Herdr can
    # render that terminal state distinctly; the next interactive start sends idle.
    try:
        ctx.on_unload(lambda: emit_status("clear"))
    except Exception as exc:
        log(f"on_unload registration failed: {exc}")
    emit_status("idle")
