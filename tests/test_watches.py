"""Synthetic Scout watch contracts, never live watches, payments or consent acceptance."""
import unittest

from test_plugin import Host

SCOUT = "scout_" + "a" * 32
TERMS = {"xpEventId": "7012", "quantity": 2, "capTotalUSD": "300", "deadlineAt": "2026-12-01T18:00:00Z", "approval": "check_first"}


def host(scopes=("scout-intents:read", "scout-intents:write"), **responses):
    reply = {"intent": {"scoutId": SCOUT, "status": "draft", "termsRevision": 1}}
    return Host({"get_connection_permissions": {"accountId": "account-1", "principalId": "p", "scopes": list(scopes)},
                 **{tool: reply for tool in ("create_scout_intent", "activate_scout_intent", "pause_scout_intent",
                                             "resume_scout_intent", "cancel_scout_intent", "respond_to_scout_intent_approval")},
                 "list_scout_intents": {"intents": []}, "get_scout_intent": {**reply, "url": "https://parlaytix.xyz/watches/" + SCOUT},
                 **responses})


def scout_calls(h):
    return [(tool, args) for _, tool, args in h.calls if tool != "get_connection_permissions"]


class WatchTests(unittest.TestCase):
    def test_ticket_permissions_alone_make_no_watch_call(self):
        h = host(scopes=("ticketing:read", "ticketing:write"))
        for tool, args in [("pepper_watches", {}), ("pepper_watch", TERMS),
                           ("pepper_watch_control", {"scoutId": SCOUT, "action": "activate", "confirmed": True})]:
            self.assertEqual(h.run(tool, args)["code"], "WATCH_CONSENT_REQUIRED")
        self.assertEqual(scout_calls(h), [])

    def test_draft_uses_exact_terms_and_one_key_per_terms(self):
        h = host()
        first = h.run("pepper_watch", TERMS)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["watch"]["status"], "draft")
        self.assertIsNone(first["payment"])
        h.run("pepper_watch", TERMS)
        h.run("pepper_watch", {**TERMS, "capTotalUSD": "250.50", "note": "aisle"})
        sent = [args for tool, args in scout_calls(h) if tool == "create_scout_intent"]
        self.assertEqual({k: v for k, v in sent[0].items() if k != "idempotencyKey"}, {**TERMS, "capTotalUSD": "300.00"})
        self.assertEqual(sent[0]["idempotencyKey"], sent[1]["idempotencyKey"])
        self.assertNotEqual(sent[0]["idempotencyKey"], sent[2]["idempotencyKey"])
        self.assertNotIn("activate", sent[0])

    def test_invalid_terms_refuse_before_any_watch_call(self):
        h = host()
        for change in [{"quantity": 9}, {"quantity": True}, {"capTotalUSD": "0"}, {"capTotalUSD": "10.999"},
                       {"approval": "auto"}, {"xpEventId": ""}, {"aimTotalUSD": "-1"}]:
            self.assertFalse(h.run("pepper_watch", {**TERMS, **change})["ok"], change)
        self.assertEqual(scout_calls(h), [])

    def test_control_and_response_need_confirmation_and_exact_ids(self):
        h = host()
        self.assertEqual(h.run("pepper_watch_control", {"scoutId": SCOUT, "action": "cancel"})["code"], "WATCH_CONFIRMATION_REQUIRED")
        self.assertFalse(h.run("pepper_watch_control", {"scoutId": SCOUT, "action": "delete", "confirmed": True})["ok"])
        self.assertFalse(h.run("pepper_watch_control", {"scoutId": "scout_1", "action": "pause", "confirmed": True})["ok"])
        self.assertEqual(h.run("pepper_watch_respond", {"scoutId": SCOUT, "decisionId": "d1", "response": "approve"})["code"],
                         "WATCH_CONFIRMATION_REQUIRED")
        self.assertEqual(scout_calls(h), [])
        for action in ("activate", "pause", "resume", "cancel"):
            self.assertTrue(h.run("pepper_watch_control", {"scoutId": SCOUT, "action": action, "confirmed": True})["ok"])
        self.assertTrue(h.run("pepper_watch_respond", {"scoutId": SCOUT, "decisionId": "d1", "response": "decline", "confirmed": True})["ok"])
        self.assertEqual(scout_calls(h), [*[(f"{a}_scout_intent", {"scoutId": SCOUT}) for a in ("activate", "pause", "resume", "cancel")],
                                          ("respond_to_scout_intent_approval", {"scoutId": SCOUT, "decisionId": "d1", "response": "decline"})])

    def test_only_a_parlaytix_payment_page_is_shown(self):
        paid = lambda url: {"intent": {"scoutId": SCOUT, "status": "draft"}, "payment": {"paymentRequestId": "pr_1", "status": "pending", "paymentUrl": url}}
        good = "https://parlaytix.xyz/pay/pr_1?returnUrl=%2Fwatches%2F" + SCOUT
        result = host(activate_scout_intent=paid(good)).run("pepper_watch_control", {"scoutId": SCOUT, "action": "activate", "confirmed": True})
        self.assertEqual(result["payment"]["paymentUrl"], good)
        self.assertIn("Pepper never pays", result["nextStep"])
        for bad in ("https://parlaytix.xyz.evil.example/pay/pr_1", "http://parlaytix.xyz/pay/pr_1", "/pay/pr_1", "https://parlaytix.xyz/claims/x", None):
            result = host(activate_scout_intent=paid(bad)).run("pepper_watch_control", {"scoutId": SCOUT, "action": "activate", "confirmed": True})
            self.assertTrue(result["ok"])
            self.assertIsNone(result["payment"]["paymentUrl"], bad)

    def test_reads_map_to_list_or_one_watch_and_claim_no_monitoring(self):
        h = host()
        self.assertEqual(h.run("pepper_watches")["watches"], {"intents": []})
        one = h.run("pepper_watches", {"scoutId": SCOUT})
        self.assertEqual(one["watches"]["url"], "https://parlaytix.xyz/watches/" + SCOUT)
        self.assertIn("monitoring health is unknown", one["guidance"])
        self.assertEqual(scout_calls(h), [("list_scout_intents", {"limit": 20}), ("get_scout_intent", {"scoutId": SCOUT})])

    def test_lost_reply_and_account_switch_never_report_success(self):
        h = host(create_scout_intent=RuntimeError("synthetic response loss"))
        lost = h.run("pepper_watch", TERMS)
        self.assertEqual((lost["code"], lost["retryAutomatically"]), ("RESULT_UNKNOWN", False))
        h = host()
        seen = []
        def permissions():
            seen.append(1)
            return {"accountId": "account-1" if len(seen) == 1 else "other", "principalId": "p", "scopes": ["scout-intents:write"]}
        h.responses["get_connection_permissions"] = permissions
        self.assertEqual(h.run("pepper_watch", TERMS)["code"], "ACCOUNT_CHANGED")


if __name__ == "__main__":
    unittest.main()
