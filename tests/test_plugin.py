"""Synthetic contracts, never live inventory or OAuth acceptance."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pepper", ROOT / "__init__.py")
pepper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pepper)


def asset():
    return {
        "claimId": "claim-1",
        "ticket": {"eventName": "Home vs Away", "eventDate": "2026-12-01T20:00:00Z",
                   "section": "101", "row": "5", "seat": "9", "backingClass": "live"},
        "condition": {"eventTitle": "Earlier Home vs Visitor", "outcomeLabel": "Visitor wins",
                      "resolutionRules": "Synthetic cancellation rule.", "outcome": "YES"},
        "tradingClosed": False, "tradingClosesAt": "2026-11-28T20:00:00Z",
        "nft": {"chainId": 8453, "actions": ["buy"], "termsHash": "terms-1"}, "isOwner": False,
        "listing": {"listingId": "listing-1", "version": 3, "price": 20.17,
                    "currency": "USD", "status": "live"},
        "pricing": {"probability": 0.31, "freshness": "fresh", "observedAt": "2026-11-08T19:00:00Z"},
    }


def checkout():
    return {
        "checkoutKind": "wallet", "claimId": "claim-1", "listingId": "listing-1",
        "listingVersion": 3, "askingPrice": 20.17, "priceUSD": 20.17,
        "confirmUrl": "https://parlaytix.xyz/claims/claim-1",
        "paymentUrl": None, "paymentRequestId": None, "receipt": None,
        "status": "awaiting_confirmation", "walletOperation": None,
    }


class MemoryState:
    def __init__(self):
        self.values = {}
        self.reads = 0

    def get(self, key):
        self.reads += 1
        return copy.deepcopy(self.values.get(key))

    def set(self, key, value):
        self.values[key] = json.loads(json.dumps(value))


class Host:
    def __init__(self, responses=None, form="structured", state=None):
        self.responses = {
            "get_connection_permissions": {"accountId": "account-1", "principalId": "principal-1",
                                           "scopes": ["ticketing:read", "ticketing:acquire"]},
            "get_ticket_asset": {"asset": asset()},
            "create_ticket_checkout": checkout(), "get_ticket_checkout": checkout(),
            **(responses or {}),
        }
        self.form = form
        self.calls = []
        self.tools = {}
        self.prompt_sections = {}
        self.state = state if state is not None else MemoryState()
        pepper.register(self)

    def register_tool(self, **definition):
        self.tools[definition["name"]] = definition["handler"]

    def register_system_prompt_section(self, section_id, content, **options):
        self.prompt_sections[section_id] = content

    def call_mcp(self, server, tool, arguments, timeout):
        self.calls.append((server, tool, arguments))
        value = self.responses[tool]
        value = value() if callable(value) else copy.deepcopy(value)
        if isinstance(value, Exception):
            raise value
        if "ok" in value:
            return value
        wrapped = {"result": value}
        if self.form == "structured":
            return {"ok": True, "result": "MCP response", "structuredContent": wrapped}
        if self.form == "object":
            return {"ok": True, "result": wrapped}
        return {"ok": True, "result": json.dumps(wrapped)}

    def run(self, name, args=None):
        return json.loads(self.tools[name]({} if args is None else args))

    def call_names(self):
        return [x[1] for x in self.calls]


class PluginTests(unittest.TestCase):
    def test_unsupported_host_refuses_before_making_product_calls(self):
        calls = []
        for context in (
            SimpleNamespace(state=MemoryState()),
            SimpleNamespace(call_mcp=lambda *args: calls.append(args)),
            SimpleNamespace(call_mcp=lambda *args: calls.append(args), state=object()),
        ):
            registered = {}
            context.register_system_prompt_section = lambda *args, **kwargs: None
            context.register_tool = lambda **tool: registered.update({tool["name"]: tool["handler"]})
            pepper.register(context)
            result = json.loads(registered["pepper_connection"]({}))
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "HERMES_API_REQUIRED")
            self.assertFalse(result["retryAutomatically"])
        self.assertEqual(calls, [])

    def selected(self, form="structured"):
        host = Host(form=form)
        result = host.run("pepper_select", {"claimId": "claim-1"})
        self.assertTrue(result["ok"], result)
        host.calls.clear()
        return host, {"selectionId": result["selection"]["selectionId"], "conditionalTicketAccepted": True}

    def test_review_uses_saved_terms_and_stable_retry_key_in_all_host_envelopes(self):
        for form in ("structured", "object", "text"):
            with self.subTest(form=form):
                host, args = self.selected(form)
                result = host.run("pepper_review", args)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["checkout"]["confirmUrl"], checkout()["confirmUrl"])
                self.assertFalse(result["purchaseCompleted"])
                self.assertEqual(host.call_names(), ["get_connection_permissions", "get_ticket_asset",
                                                    "create_ticket_checkout", "get_connection_permissions"])
                requested = [x[2] for x in host.calls if x[1] == "create_ticket_checkout"][0]
                self.assertEqual(requested, {"listingId": "listing-1", "expectedListingVersion": 3,
                                            "idempotencyKey": "pepper-review-" + args["selectionId"]})
                restarted = Host(state=host.state)
                self.assertTrue(restarted.run("pepper_review", args)["ok"])
                self.assertEqual([x[2] for x in restarted.calls if x[1] == "create_ticket_checkout"], [requested])

    def test_changed_listing_ticket_and_outcome_never_request_a_handoff(self):
        mutations = [("listing", "version", 4), ("listing", "price", "20.18"),
                     ("listing", "currency", "EUR"), ("listing", "listingId", "other"),
                     ("ticket", "eventDate", "2026-12-02T20:00:00Z"), ("ticket", "seat", "10"),
                     ("condition", "outcomeLabel", "Other result"), ("condition", "resolutionRules", "New rules"),
                     ("nft", "termsHash", "terms-2"), (None, "tradingClosesAt", "2026-11-27T20:00:00Z"),
                     (None, "claimId", "other")]
        for group, field, value in mutations:
            with self.subTest(field=field):
                host, args = self.selected()
                item = host.responses["get_ticket_asset"]["asset"]
                (item[group] if group else item)[field] = value
                self.assertFalse(host.run("pepper_review", args)["ok"])
                self.assertNotIn("create_ticket_checkout", host.call_names())

    def test_unavailable_and_missing_terms_remain_distinct(self):
        cases = [(None, "isOwner", True, "OWN_LISTING"),
                 (None, "tradingClosed", True, "TRADING_CLOSED"),
                 ("listing", "status", "closed", "CARD_UNAVAILABLE"),
                 ("nft", "actions", "buy", "CARD_UNAVAILABLE"),
                 ("nft", "chainId", 1, "UNSUPPORTED_CHAIN"),
                 ("ticket", "backingClass", "rehearsal", "CARD_UNAVAILABLE"),
                 ("condition", "eventTitle", None, "MISSING_TERMS")]
        for group, field, value, expected in cases:
            with self.subTest(expected=expected, field=field):
                host = Host()
                item = host.responses["get_ticket_asset"]["asset"]
                (item[group] if group else item)[field] = value
                selected = host.run("pepper_select", {"claimId": "claim-1"})
                self.assertTrue(selected["ok"])
                result = host.run("pepper_review", {"selectionId": selected["selection"]["selectionId"],
                                                   "conditionalTicketAccepted": True})
                self.assertEqual(result["code"], expected)
                self.assertNotIn("create_ticket_checkout", host.call_names())

    def test_consent_and_malformed_input_are_rejected_before_network(self):
        for args in ({"selectionId": "missing", "conditionalTicketAccepted": False},
                     {"selectionId": "", "conditionalTicketAccepted": True},
                     {"selectionId": True, "conditionalTicketAccepted": True}, []):
            host = Host()
            self.assertFalse(host.run("pepper_review", args)["ok"])
            self.assertEqual(host.calls, [])

    def test_failed_truncated_and_unparseable_results_never_advance(self):
        bad_results = [{"ok": False, "error": "SECRET must not escape"},
                       {"ok": True, "truncated": True, "result": {"asset": asset()}},
                       {"ok": True, "result": "Not valid JSON"},
                       {"ok": True, "result": {"error": "permission denied"}},
                       PermissionError("SECRET must not escape")]
        for response in bad_results:
            host, args = self.selected()
            host.responses["get_ticket_asset"] = response
            result = host.run("pepper_review", args)
            self.assertFalse(result["ok"])
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertNotIn("create_ticket_checkout", host.call_names())

    def test_bad_handoff_identity_price_and_url_are_not_returned(self):
        changes = [{"claimId": "other"}, {"listingId": "other"}, {"listingVersion": 4},
                   {"askingPrice": 22}, {"checkoutKind": "hosted"},
                   {"confirmUrl": "https://parlaytix.xyz.evil.example/claims/claim-1"},
                   {"confirmUrl": "https://parlaytix.xyz/claims/claim-1?token=secret"},
                   {"confirmUrl": "https://parlaytix.xyz/claims/other"},
                   {"paymentUrl": "https://example.com/payment"}]
        for change in changes:
            host, args = self.selected()
            host.responses["create_ticket_checkout"].update(change)
            result = host.run("pepper_review", args)
            self.assertFalse(result["ok"])
            self.assertNotIn("checkout", result)

    def test_stock_approval_denial_and_reauth_preserve_stop_instructions(self):
        failures = [
            ("The user did not approve running write-capable MCP tool 'get_ticket_asset' on untrusted server 'parlaytix-prod'. The command was NOT run. Do not retry without explicit user direction.", "APPROVAL_DENIED"),
            ("MCP tool 'get_ticket_asset' on untrusted server 'parlaytix-prod' was blocked: the approval system was unavailable (fail-closed).", "APPROVAL_UNAVAILABLE"),
            ("MCP server 'parlaytix-prod' requires re-authentication. SECRET provider detail", "NEEDS_REAUTH"),
        ]
        for error, code in failures:
            host, args = self.selected()
            host.responses["get_ticket_asset"] = {"ok": False, "error": error}
            result = host.run("pepper_review", args)
            self.assertEqual(result["code"], code)
            self.assertFalse(result["retryAutomatically"])
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertEqual(host.call_names()[-1], "get_ticket_asset")

    def test_unknown_errors_never_suggest_automatic_retry(self):
        for error in ({"ok": False, "error": "denied reauth SECRET arbitrary upstream text"},
                      RuntimeError("SECRET token transport failure")):
            host, args = self.selected()
            host.responses["create_ticket_checkout"] = error
            result = host.run("pepper_review", args)
            self.assertEqual(result["code"], "RESULT_UNKNOWN")
            self.assertFalse(result["retryAutomatically"])
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertEqual(host.call_names().count("create_ticket_checkout"), 1)
            restarted = Host(state=host.state)
            self.assertTrue(restarted.run("pepper_resume")["ok"])
            self.assertNotIn("create_ticket_checkout", restarted.call_names())

    def test_status_requires_matching_completed_receipt_for_ownership(self):
        host = Host()
        data = host.responses["get_ticket_checkout"]
        data["status"] = "confirmed"
        self.assertFalse(host.run("pepper_status", {"claimId": "claim-1"})["purchaseCompleted"])
        data["status"] = "completed"
        data["receipt"] = {"claimId": "other", "listingId": "listing-1", "status": "completed",
                           "ownershipTransferStatus": "completed"}
        self.assertFalse(host.run("pepper_status", {"claimId": "claim-1"})["ok"])
        data["receipt"]["claimId"] = "claim-1"
        self.assertTrue(host.run("pepper_status", {"claimId": "claim-1"})["purchaseCompleted"])
        data["receipt"]["listingId"] = "another-listing"
        self.assertFalse(host.run("pepper_status", {"claimId": "claim-1"})["ok"])

    def test_find_uses_explicit_query_and_preserves_coverage(self):
        host = Host({"search_ticket_events": {"items": [], "coverageComplete": False},
                     "search_ticket_listings": {"items": [asset()], "page": 1, "hasMore": True}})
        self.assertFalse(host.run("pepper_find", {"query": " "})["ok"])
        self.assertEqual(host.calls, [])
        result = host.run("pepper_find", {"query": "Home", "page": 1})
        self.assertTrue(result["listings"]["hasMore"])
        self.assertFalse(result["events"]["coverageComplete"])
        self.assertEqual(host.calls[0][2], {"query": "Home"})

    def test_restart_restores_selection_and_reads_fresh_status_without_repeating_review(self):
        host, args = self.selected()
        restarted = Host(state=host.state)
        result = restarted.run("pepper_resume")
        self.assertTrue(result["ok"])
        self.assertEqual(result["selection"]["selectionId"], args["selectionId"])
        self.assertFalse(result["termsChanged"])
        self.assertFalse(result["status"]["purchaseCompleted"])
        self.assertIn("get_ticket_checkout", restarted.call_names())
        self.assertNotIn("create_ticket_checkout", restarted.call_names())

    def test_new_account_and_new_profile_cannot_read_another_selection(self):
        host, args = self.selected()
        other = Host(state=host.state)
        other.responses["get_connection_permissions"]["accountId"] = "account-2"
        result = other.run("pepper_resume")
        self.assertIsNone(result["selection"])
        self.assertNotIn("claim-1", json.dumps(result))
        self.assertEqual(other.run("pepper_review", args)["code"], "SELECTION_REQUIRED")
        self.assertIsNone(Host().run("pepper_resume")["selection"])
        self.assertEqual(Host(state=host.state).run("pepper_resume")["selection"]["selectionId"], args["selectionId"])

    def test_revoked_or_missing_permission_cannot_read_saved_state(self):
        host, _ = self.selected()
        host.state.reads = 0
        host.responses["get_connection_permissions"] = {"ok": False, "error": "MCP server 'parlaytix-prod' requires re-authentication."}
        self.assertEqual(host.run("pepper_resume")["code"], "NEEDS_REAUTH")
        self.assertEqual(host.state.reads, 0)
        host.responses["get_connection_permissions"] = {"accountId": "account-1", "scopes": []}
        self.assertEqual(host.run("pepper_resume")["code"], "SCOPE_REQUIRED")
        self.assertEqual(host.state.reads, 0)

    def test_account_switch_during_request_never_saves_or_returns_private_selection(self):
        for operation in ("pepper_select", "pepper_resume", "pepper_review"):
            with self.subTest(operation=operation):
                host, args = self.selected()
                before = copy.deepcopy(host.state.values)
                responses = iter([{"accountId": account, "scopes": ["ticketing:read", "ticketing:acquire"]}
                                  for account in ("account-1", "account-2")])
                host.responses["get_connection_permissions"] = lambda: next(responses)
                inputs = args if operation == "pepper_review" else {"claimId": "claim-1"} if operation == "pepper_select" else {}
                result = host.run(operation, inputs)
                self.assertEqual(result["code"], "ACCOUNT_CHANGED")
                self.assertNotIn("selection", result)
                self.assertNotIn("checkout", result)
                self.assertEqual(host.state.values, before)

    def test_resume_never_silently_accepts_changed_terms(self):
        host, args = self.selected()
        before = copy.deepcopy(host.state.values)
        host.responses["get_ticket_asset"]["asset"]["listing"]["price"] = "21.00"
        result = host.run("pepper_resume")
        self.assertTrue(result["termsChanged"])
        self.assertEqual(result["selection"]["card"]["listing"]["price"], "20.17")
        self.assertEqual(result["currentCard"]["listing"]["price"], "21.00")
        self.assertEqual(host.state.values, before)
        self.assertEqual(host.run("pepper_review", args)["code"], "TERMS_CHANGED")
        replacement = host.run("pepper_select", {"claimId": "claim-1"})
        self.assertNotEqual(replacement["selection"]["selectionId"], args["selectionId"])
        self.assertEqual(host.run("pepper_review", args)["code"], "SELECTION_REQUIRED")

    def test_price_observations_are_not_ticket_terms_or_prices(self):
        host, _ = self.selected()
        item = host.responses["get_ticket_asset"]["asset"]
        item["pricing"].update(probability=0.8, freshness="stale")
        item["listing"]["price"] = "20.170"
        result = host.run("pepper_resume")
        self.assertFalse(result["termsChanged"])
        self.assertIsNone(result["currentCard"]["pricing"]["probability"])
        self.assertEqual(result["currentCard"]["listing"]["price"], "20.170")

    def test_projection_preserves_distinct_events_and_never_stores_raw_private_fields(self):
        host = Host()
        item = host.responses["get_ticket_asset"]["asset"]
        item["walletAddress"] = "PRIVATE"
        item["nft"]["privateKey"] = "PRIVATE"
        item["ticket"]["transferSecret"] = "PRIVATE"
        result = host.run("pepper_select", {"claimId": "claim-1"})
        card = result["selection"]["card"]
        self.assertEqual(card["ticket"]["eventName"], "Home vs Away")
        self.assertEqual(card["condition"]["eventTitle"], "Earlier Home vs Visitor")
        self.assertEqual(card["outcomes"]["voidOrCancelled"], "Synthetic cancellation rule.")
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("PRIVATE", json.dumps(host.state.values))

    def test_forget_only_clears_the_current_account_selection(self):
        host, _ = self.selected()
        second = Host(state=host.state)
        second.responses["get_connection_permissions"]["accountId"] = "account-2"
        second.run("pepper_select", {"claimId": "claim-1"})
        self.assertTrue(host.run("pepper_forget")["ok"])
        self.assertIsNone(host.run("pepper_resume")["selection"])
        self.assertIsNotNone(second.run("pepper_resume")["selection"])
        self.assertNotIn("create_ticket_checkout", host.call_names())

    def test_corrupt_and_failed_storage_refuse_without_erasing_data(self):
        host, _ = self.selected()
        key = next(iter(host.state.values))
        host.state.values[key]["schemaVersion"] = 999
        before = copy.deepcopy(host.state.values)
        self.assertEqual(host.run("pepper_resume")["code"], "STATE_INVALID")
        self.assertEqual(host.state.values, before)
        def broken(*args):
            raise RuntimeError("PRIVATE storage error")
        host.state.get = broken
        result = host.run("pepper_resume")
        self.assertEqual(result["code"], "STATE_UNAVAILABLE")
        self.assertNotIn("PRIVATE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
