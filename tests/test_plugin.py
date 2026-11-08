import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pepper", ROOT / "__init__.py")
pepper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pepper)


def asset():
    return {
        "claimId": "claim-1", "ticket": {"eventName": "Home vs Away", "backingClass": "live"},
        "condition": {"outcomeLabel": "Away wins"}, "tradingClosed": False,
        "nft": {"chainId": 8453, "actions": ["buy"]}, "isOwner": False,
        "listing": {"listingId": "listing-1", "version": 3, "price": 20.17,
                    "currency": "USD", "status": "live"},
    }


def checkout():
    return {
        "checkoutKind": "wallet", "claimId": "claim-1", "listingId": "listing-1",
        "listingVersion": 3, "askingPrice": 20.17, "priceUSD": 20.17,
        "confirmUrl": "https://parlaytix.xyz/claims/claim-1",
        "paymentUrl": None, "paymentRequestId": None, "receipt": None,
        "status": "awaiting_confirmation", "walletOperation": None,
    }


TERMS = {
    "claimId": "claim-1", "listingId": "listing-1", "expectedListingVersion": 3,
    "expectedPrice": "20.17", "currency": "USD", "idempotencyKey": "review-1",
    "conditionalTicketAccepted": True,
}


class Host:
    def __init__(self, responses, form="structured"):
        self.responses = responses
        self.form = form
        self.calls = []
        self.tools = {}
        pepper.register(self)

    def register_tool(self, **definition):
        self.tools[definition["name"]] = definition["handler"]

    def call_mcp(self, server, tool, arguments, timeout):
        self.calls.append((server, tool, arguments))
        value = copy.deepcopy(self.responses[tool])
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

    def run(self, name, args):
        return json.loads(self.tools[name](args))


class PluginTests(unittest.TestCase):
    def host(self, form="structured"):
        return Host({"get_ticket_asset": {"asset": asset()},
                     "create_ticket_checkout": checkout(), "get_ticket_checkout": checkout()}, form)

    def test_review_refreshes_and_preserves_accepted_terms_for_all_host_envelopes(self):
        for form in ("structured", "object", "text"):
            with self.subTest(form=form):
                host = self.host(form)
                result = host.run("pepper_review", TERMS)
                self.assertTrue(result["ok"])
                self.assertEqual(result["checkout"]["confirmUrl"], checkout()["confirmUrl"])
                self.assertFalse(result["purchaseCompleted"])
                self.assertEqual([x[1] for x in host.calls], ["get_ticket_asset", "create_ticket_checkout"])
                self.assertEqual(host.calls[-1][2], {"listingId": "listing-1", "expectedListingVersion": 3, "idempotencyKey": "review-1"})

    def test_changed_terms_and_refused_cards_never_request_a_handoff(self):
        mutations = [("listing", "version", 4), ("listing", "price", "20.18"),
                     ("listing", "currency", "EUR"), ("listing", "listingId", "other"),
                     ("listing", "status", "closed"), (None, "claimId", "other"),
                     (None, "tradingClosed", True), (None, "isOwner", True),
                     ("nft", "actions", []), ("nft", "actions", "buy"),
                     ("ticket", "backingClass", "rehearsal")]
        for group, field, value in mutations:
            with self.subTest(field=field, value=value):
                host = self.host()
                item = host.responses["get_ticket_asset"]["asset"]
                (item[group] if group else item)[field] = value
                self.assertFalse(host.run("pepper_review", TERMS)["ok"])
                self.assertEqual(len(host.calls), 1)

    def test_consent_and_malformed_input_are_rejected_before_network(self):
        for changes in ({"conditionalTicketAccepted": False}, {"expectedPrice": "NaN"},
                        {"expectedListingVersion": True}, {"idempotencyKey": ""}):
            host = self.host()
            self.assertFalse(host.run("pepper_review", {**TERMS, **changes})["ok"])
            self.assertEqual(host.calls, [])

    def test_failed_truncated_and_unparseable_results_never_advance(self):
        bad_results = [{"ok": False, "error": "SECRET must not escape"},
                       {"ok": True, "truncated": True, "result": {"asset": asset()}},
                       {"ok": True, "result": "Not valid JSON"},
                       {"ok": True, "result": {"error": "permission denied"}},
                       PermissionError("SECRET must not escape")]
        for response in bad_results:
            host = self.host()
            host.responses["get_ticket_asset"] = response
            result = host.run("pepper_review", TERMS)
            self.assertFalse(result["ok"])
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertEqual(len(host.calls), 1)

    def test_bad_handoff_identity_price_and_url_are_not_returned(self):
        changes = [{"claimId": "other"}, {"listingId": "other"}, {"listingVersion": 4},
                   {"askingPrice": 22}, {"checkoutKind": "hosted"},
                   {"confirmUrl": "https://parlaytix.xyz.evil.example/claims/claim-1"},
                   {"confirmUrl": "https://parlaytix.xyz/claims/claim-1?token=secret"},
                   {"confirmUrl": "https://parlaytix.xyz/claims/other"},
                   {"paymentUrl": "https://example.com/payment"}]
        for change in changes:
            host = self.host()
            host.responses["create_ticket_checkout"].update(change)
            result = host.run("pepper_review", TERMS)
            self.assertFalse(result["ok"])
            self.assertNotIn("checkout", result)

    def test_stock_approval_denial_and_reauth_stop_the_flow_without_raw_errors(self):
        failures = [
            ("The user did not approve running write-capable MCP tool 'get_ticket_asset' on untrusted server 'parlaytix-prod'. The command was NOT run. Do not retry without explicit user direction.", "APPROVAL_DENIED", "explicit user direction"),
            ("MCP tool 'get_ticket_asset' on untrusted server 'parlaytix-prod' was blocked: the approval system was unavailable (fail-closed).", "APPROVAL_UNAVAILABLE", "interactive approval"),
            ("MCP server 'parlaytix-prod' requires re-authentication. SECRET provider detail", "NEEDS_REAUTH", "hermes mcp login parlaytix-prod"),
        ]
        for error, code, instruction in failures:
            with self.subTest(code=code):
                host = self.host()
                host.responses["get_ticket_asset"] = {"ok": False, "error": error}
                result = host.run("pepper_review", TERMS)
                self.assertEqual(result["code"], code)
                self.assertFalse(result["retryAutomatically"])
                self.assertIn(instruction, result["error"])
                self.assertNotIn("SECRET", json.dumps(result))
                self.assertEqual(len(host.calls), 1)

    def test_unknown_errors_never_suggest_automatic_retry_or_infer_error_categories(self):
        for error in ({"ok": False, "error": "denied reauth SECRET arbitrary upstream text"},
                      RuntimeError("SECRET token transport failure")):
            host = self.host()
            host.responses["get_ticket_asset"] = error
            result = host.run("pepper_review", TERMS)
            self.assertEqual(result["code"], "RESULT_UNKNOWN")
            self.assertFalse(result["retryAutomatically"])
            self.assertIn("Do not automatically retry", result["error"])
            self.assertIn("Inspect ParlayTix status", result["error"])
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertEqual(len(host.calls), 1)

    def test_status_requires_matching_completed_receipt_for_ownership(self):
        host = self.host()
        data = host.responses["get_ticket_checkout"]
        data["status"] = "confirmed"
        self.assertFalse(host.run("pepper_status", {"claimId": "claim-1"})["purchaseCompleted"])
        data["status"] = "completed"
        data["receipt"] = {"claimId": "other", "listingId": "listing-1", "status": "completed", "ownershipTransferStatus": "completed"}
        self.assertFalse(host.run("pepper_status", {"claimId": "claim-1"})["ok"])
        data["receipt"]["claimId"] = "claim-1"
        self.assertTrue(host.run("pepper_status", {"claimId": "claim-1"})["purchaseCompleted"])
        data["receipt"]["listingId"] = "another-listing"
        self.assertFalse(host.run("pepper_status", {"claimId": "claim-1"})["ok"])

    def test_find_uses_explicit_query_and_preserves_coverage(self):
        host = Host({"search_ticket_events": {"items": [], "coverageComplete": False, "searchRequired": False},
                     "search_ticket_listings": {"items": [asset()], "page": 1, "hasMore": True}})
        self.assertFalse(host.run("pepper_find", {"query": " "})["ok"])
        self.assertEqual(host.calls, [])
        result = host.run("pepper_find", {"query": "Home", "page": 1})
        self.assertTrue(result["listings"]["hasMore"])
        self.assertFalse(result["events"]["coverageComplete"])
        self.assertEqual(host.calls[0][2], {"query": "Home"})

    def test_connection_uses_only_native_host_api(self):
        host = Host({"get_connection_permissions": {"accountId": "account-1", "principalId": "principal-1", "scopes": ["ticketing:read"]}})
        result = host.run("pepper_connection", {})
        self.assertTrue(result["ok"])
        self.assertEqual(host.calls, [("parlaytix-prod", "get_connection_permissions", {})])


if __name__ == "__main__":
    unittest.main()
