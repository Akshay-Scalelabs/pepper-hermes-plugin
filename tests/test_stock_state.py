"""Run in stock Hermes to test its real storage with synthetic MCP responses."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_plugin import Host, pepper
from test_parlay import host as holder_host, draft, confirmed


@unittest.skipUnless(importlib.util.find_spec("hermes_cli"), "Requires the stock Hermes runtime")
class StockStateTests(unittest.TestCase):
    def test_guided_entry_is_rendered_by_native_prompt_sections(self):
        from hermes_cli.plugins import PluginContext, PluginManager
        from hermes_cli.plugins_manifest import PluginManifest

        with tempfile.TemporaryDirectory(prefix="pepper-prompt-") as home, \
                patch.dict(os.environ, {"HERMES_HOME": home}):
            manager = PluginManager()
            ctx = PluginContext(PluginManifest(name="pepper"), manager)
            pepper.register(ctx)
            sections = manager.render_system_prompt_sections({"session_id": "new-discord-chat"})
            journey = next(section for section in sections if section.id == "pepper.journey")
            self.assertEqual(journey.content, pepper.JOURNEY_PROMPT)

    def test_native_state_survives_new_context_and_isolates_profiles(self):
        from hermes_cli.plugins import PluginContext
        from hermes_cli.plugins_manifest import PluginManifest

        def context():
            return PluginContext(PluginManifest(name="pepper"), SimpleNamespace())

        with tempfile.TemporaryDirectory(prefix="pepper-state-") as first, \
                tempfile.TemporaryDirectory(prefix="pepper-state-") as second:
            with patch.dict(os.environ, {"HERMES_HOME": first}):
                original = Host(state=context().state)
                selected = original.run("pepper_select", {"claimId": "claim-1"})
                self.assertTrue(selected["ok"])
                fresh = Host(state=context().state)
                resumed = fresh.run("pepper_resume")
                self.assertEqual(resumed["selection"]["selectionId"], selected["selection"]["selectionId"])
                self.assertNotIn("create_ticket_checkout", fresh.call_names())
                holder = holder_host(state=context().state)
                parlay = draft(holder)
                holder.responses["create_ticket_split"] = RuntimeError("synthetic response loss")
                self.assertEqual(holder.run("pepper_split", confirmed(parlay))["code"], "RESULT_UNKNOWN")
                missed = holder_host(state=context().state)
                missed.responses["get_ticket_split"] = {"ok": False, "error": "TICKET_SPLIT_NOT_FOUND (HTTP 404)"}
                self.assertTrue(missed.run("pepper_parlay_status")["outcomeUnknown"])
                recovered = holder_host(state=context().state)
                self.assertTrue(recovered.run("pepper_parlay_status")["splitCompleted"])
                self.assertNotIn("create_ticket_split", recovered.call_names())
                files = list(Path(first).glob("plugin-data/*/state.json"))
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            with patch.dict(os.environ, {"HERMES_HOME": second}):
                self.assertIsNone(Host(state=context().state).run("pepper_resume")["selection"])
                self.assertIsNone(holder_host(state=context().state).run("pepper_parlay_status")["draft"])
            with patch.dict(os.environ, {"HERMES_HOME": first}):
                restored = Host(state=context().state)
                self.assertEqual(restored.run("pepper_resume")["selection"]["selectionId"],
                                 selected["selection"]["selectionId"])
                self.assertTrue(restored.run("pepper_forget")["ok"])
                self.assertIsNone(Host(state=context().state).run("pepper_resume")["selection"])


if __name__ == "__main__":
    unittest.main()
