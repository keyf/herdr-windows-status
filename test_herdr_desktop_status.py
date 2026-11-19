"""Regression checks for the Herdr client-side status bridge."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

PLUGIN = Path(__file__).with_name("__init__.py")


def load_plugin(gateway_agent_class):
    package = types.ModuleType("hermes_cli")
    package.__path__ = []
    gateway_agent = types.ModuleType("hermes_cli.gateway_agent")
    gateway_agent.GatewayAgent = gateway_agent_class
    # Hermes dispatches live events through this module-level table, which snapshots
    # method objects at import time instead of looking them up on GatewayAgent.
    gateway_agent._HANDLERS = {"approval.request": gateway_agent_class._on_approval}
    spec = importlib.util.spec_from_file_location("herdr_status_under_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    with patch.dict(sys.modules, {"hermes_cli": package, "hermes_cli.gateway_agent": gateway_agent}):
        spec.loader.exec_module(module)
    return module, package, gateway_agent


class HerdrStatusBridgeTests(unittest.TestCase):
    def _agent(self, result=None):
        class GatewayAgent:
            def run_conversation(self):
                return result

            def _answer_request(self, frame):
                return frame

            def _on_approval(self, payload, stream_callback, starts):
                self.approval_seen = payload
                return "once"

        return GatewayAgent

    def _install_and_record(self, result=None):
        gateway = self._agent(result)
        plugin, package, gateway_agent = load_plugin(gateway)
        events = []
        plugin.emit_status = lambda *event: events.append(event)
        with patch.dict(sys.modules, {"hermes_cli": package, "hermes_cli.gateway_agent": gateway_agent}):
            self.assertTrue(plugin._install_gateway_client_bridge())
            agent = gateway()
        return plugin, agent, events, gateway_agent

    def test_successful_turn_reports_done_with_reply_headline(self):
        _, agent, events, _ = self._install_and_record({"final_response": "Finished the report\nDetails"})
        agent.run_conversation()
        self.assertEqual(events, [("working",), ("done", None)])

    def test_failed_turn_reports_error_with_sanitized_message(self):
        _, agent, events, _ = self._install_and_record({"failed": True, "error": "bad\x1b]2;title\x07 gateway"})
        agent.run_conversation()
        self.assertEqual(events, [("working",), ("error", None)])

    def test_command_approval_reports_permission_when_dispatched_through_hermes_handler_table(self):
        plugin, agent, events, gateway_agent = self._install_and_record()
        # This is Hermes's real event path; calling agent._on_approval directly
        # would miss the import-time handler-table binding that caused the bug.
        handler = gateway_agent._HANDLERS["approval.request"]
        self.assertEqual(handler(agent, {"command": "rm -rf build", "description": "Remove build output"}, None, {}), "once")
        self.assertEqual(events, [("blocked", "permission"), ("working",)])

    def test_clarify_request_reports_question_text_until_resolved(self):
        _, agent, events, _ = self._install_and_record()
        frame = {"method": "clarify", "params": {"questions": [{"question": "Which environment?"}]}}
        self.assertEqual(agent._answer_request(frame), frame)
        self.assertEqual(events, [("blocked", "question"), ("working",)])

    def test_direct_cli_clarify_and_sudo_callbacks_are_reported(self):
        gateway = self._agent()
        plugin, package, gateway_agent = load_plugin(gateway)
        modal = types.ModuleType("hermes_cli.cli_modal_mixin")

        class CLIModalMixin:
            def _clarify_callback(self, questions):
                return questions

            def _sudo_password_callback(self):
                return "secret"

        modal.CLIModalMixin = CLIModalMixin
        events = []
        plugin.emit_status = lambda *event: events.append(event)
        with patch.dict(sys.modules, {
            "hermes_cli": package,
            "hermes_cli.gateway_agent": gateway_agent,
            "hermes_cli.cli_modal_mixin": modal,
        }):
            self.assertTrue(plugin._install_modal_bridge())
            client = CLIModalMixin()
            self.assertEqual(client._clarify_callback([{"question": "Select target"}]), [{"question": "Select target"}])
            self.assertEqual(client._sudo_password_callback(), "secret")

        self.assertEqual(events, [
            ("blocked", "question"), ("working",),
            ("blocked", "auth"), ("working",),
        ])

    def test_windows_wire_payload_omits_context_by_default(self):
        gateway = self._agent()
        plugin, _, _ = load_plugin(gateway)
        console = mock_open()
        with patch("builtins.open", console):
            plugin.emit_status("blocked", "permission", "Approve deploy?")
        written = console.return_value.write.call_args.args[0]
        self.assertEqual(written, "\x1b]7501;state=blocked:app=hermes:kind=permission\x07")


if __name__ == "__main__":
    unittest.main()
