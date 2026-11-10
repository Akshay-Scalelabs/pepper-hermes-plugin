"""Synthetic holder workflow contracts; no live ticket or mint acceptance claim."""
import copy
import unittest
from test_plugin import Host, MemoryState, pepper


def preview():
    return {"collateralId": "tc_owned", "splitKind": "complementary_ticket_rights", "createsListings": False, "splitEnabled": True,
            "ticket": {"collateralId": "tc_owned", "eventName": "Final", "eventDate": "2099-01-01", "backingClass": "live", "seat": "1"},
            "admission": {"eligible": True, "universeHash": "a" * 64}, "previewHash": "0x" + "b" * 64,
            "outcomes": [{"outcomeId": "condition:" + side, "outcome": side, "label": side,
                          "description": "Earlier game", "resolutionRules": "Synthetic resolution and cancellation rules.",
                          "resolutionAt": "2098-12-01", "probability": chance, "observedAt": "2026-11-09", "freshness": "fresh"}
                         for side, chance in [("YES", 0.6), ("NO", 0.4)]]}


def status(ready=True):
    return {"splitId": "split_1", "rootCollateralId": "tc_owned", "splitCompleted": ready, "status": "ready" if ready else "minting",
            "assets": [{"claimId": "claim_" + side, "rootCollateralId": "tc_owned", "isOwner": True,
                        "condition": {"outcomeId": "condition:" + side}, "listing": None,
                        "nft": {"mintStatus": "minted", "termsHash": "terms_" + side, "actions": ["list"]}}
                       for side in ["YES", "NO"]] if ready else []}


def host(state=None):
    return Host({"get_connection_permissions": {"accountId": "holder", "scopes": ["ticketing:read", "ticketing:write"]},
                 "preview_ticket_split": preview(), "create_ticket_split": {"splitId": "split_1"}, "get_ticket_split": status(),
                 "list_my_ticket_inventory": {"items": [{"collateralId": "tc_owned", "canSplit": True}]},
                 "list_my_ticket_assets": {"items": []}}, state=state)


def draft(h):
    return h.run("pepper_parlay", {"collateralId": "tc_owned", "eventId": "123"})["draft"]


def confirmed(d):
    return {"draftId": d["draftId"], "keepOutcomeId": "condition:YES", "confirmed": True}


class ParlayTests(unittest.TestCase):
    def test_short_entry_needs_no_account_read_and_preserves_an_existing_draft(self):
        h = host(); draft(h); before = copy.deepcopy(h.state.values)
        h.calls.clear()
        h.responses['get_connection_permissions'] = RuntimeError('not signed in')
        result = h.run('pepper_start')
        self.assertTrue(result['ok'])
        self.assertEqual(result['stage'], 'choose_ticket_path')
        self.assertEqual([choice['nextTool'] for choice in result['choices']], ['pepper_owned', 'pepper_find'])
        self.assertEqual(h.call_names(), [])
        self.assertEqual(h.state.values, before)

    def test_disabled_issuance_and_corrupt_state_refuse_without_writes(self):
        h = host(); d = draft(h)
        h.responses["preview_ticket_split"]["splitEnabled"] = False
        self.assertEqual(h.run("pepper_split", confirmed(d))["code"], "SPLIT_UNAVAILABLE")
        next(iter(h.state.values.values())).pop("source")
        self.assertEqual(h.run("pepper_parlay_status")["code"], "STATE_INVALID")
        self.assertNotIn("create_ticket_split", h.call_names())

    def test_forget_requires_exact_confirmation_and_preserves_pending_recovery(self):
        h = host(); d = draft(h)
        self.assertFalse(h.run("pepper_parlay_forget", {"draftId": d["draftId"], "confirmed": False})["ok"])
        h.run("pepper_split", confirmed(d))
        self.assertEqual(h.run("pepper_parlay_forget", {"draftId": d["draftId"], "confirmed": True})["code"], "PARLAY_PENDING")
        h.run("pepper_parlay_status")
        self.assertTrue(h.run("pepper_parlay_forget", {"draftId": d["draftId"], "confirmed": True})["ok"])
        self.assertIsNone(h.run("pepper_parlay_status")["draft"])

    def test_owned_preview_and_confirmed_split_use_the_same_terms_without_listing(self):
        h = host()
        self.assertTrue(h.run("pepper_owned")["wholeTickets"]["items"][0]["canSplit"])
        d = draft(h)
        self.assertNotIn("create_ticket_split", h.call_names())
        self.assertFalse(h.run("pepper_split", {**confirmed(d), "confirmed": False})["ok"])
        self.assertFalse(h.run("pepper_split", {**confirmed(d), "keepOutcomeId": "other"})["ok"])
        self.assertFalse(h.run("pepper_split", confirmed(d))["splitCompleted"])
        created = [call[2] for call in h.calls if call[1] == "create_ticket_split"]
        self.assertEqual(created, [{"collateralId": "tc_owned", "eventId": "123", "universeHash": "a" * 64,
                                   "previewHash": "0x" + "b" * 64, "confirmed": True, "idempotencyKey": "pepper-" + d["draftId"]}])
        self.assertTrue(h.run("pepper_split", confirmed(d))["splitCompleted"])
        self.assertEqual(h.call_names().count("create_ticket_split"), 1)

    def test_changed_terms_require_a_new_preview_but_odds_alone_do_not(self):
        for change in ["hash", "rules", "seat", "odds"]:
            h = host(); d = draft(h)
            p = h.responses["preview_ticket_split"]
            if change == "hash": p["previewHash"] = "0x" + "c" * 64
            if change == "rules": p["outcomes"][0]["resolutionRules"] = "Different rules"
            if change == "seat": p["ticket"]["seat"] = "2"
            if change == "odds": p["outcomes"][0]["probability"] = 0.5
            result = h.run("pepper_split", confirmed(d))
            self.assertEqual(result["ok"], change == "odds")
            self.assertEqual("create_ticket_split" in h.call_names(), change == "odds")

    def test_lost_response_is_recovered_after_restart_by_reading_original_request(self):
        state = MemoryState(); h = host(state); d = draft(h)
        h.responses["create_ticket_split"] = RuntimeError("private transport detail")
        result = h.run("pepper_split", confirmed(d))
        self.assertEqual(result["code"], "RESULT_UNKNOWN")
        self.assertNotIn("private", result["error"])
        restarted = host(state)
        self.assertTrue(restarted.run("pepper_parlay_status")["splitCompleted"])
        reads = [call[2] for call in restarted.calls if call[1] == "get_ticket_split"]
        self.assertEqual(reads, [{"idempotencyKey": "pepper-" + d["draftId"]}])
        self.assertNotIn("create_ticket_split", restarted.call_names())

    def test_pending_split_cannot_be_overwritten_or_listed(self):
        h = host(); d = draft(h); h.run("pepper_split", confirmed(d))
        h.responses["get_ticket_split"] = status(False)
        self.assertEqual(h.run("pepper_parlay", {"collateralId": "another", "eventId": "123"})["code"], "PARLAY_PENDING")
        self.assertEqual(h.run("pepper_offer", {"draftId": d["draftId"], "askingPrice": "25"})["code"], "SPLIT_PENDING")

    def test_offer_preserves_keep_side_and_requires_actual_listing_at_expected_price(self):
        h = host(); d = draft(h); h.run("pepper_split", confirmed(d))
        result = h.run("pepper_offer", {"draftId": d["draftId"], "askingPrice": "25.00"})
        self.assertEqual(result["offer"]["reviewUrl"], "https://parlaytix.xyz/claims/claim_NO")
        self.assertEqual(result["keptOutcomeId"], "condition:YES")
        self.assertFalse(result["listingCreated"])
        self.assertFalse(h.run("pepper_parlay_status")["listingObserved"])
        h.responses["get_ticket_split"]["assets"][1]["listing"] = {"status": "live", "price": 26}
        result = h.run("pepper_parlay_status")
        self.assertTrue(result["listingObserved"])
        self.assertFalse(result["listingMatchesDraft"])
        h.responses["get_ticket_split"]["assets"][1]["listing"]["price"] = 25
        self.assertTrue(h.run("pepper_parlay_status")["listingMatchesDraft"])
        self.assertEqual(set(h.call_names()) - {"get_connection_permissions", "preview_ticket_split", "create_ticket_split", "get_ticket_split"}, set())

    def test_wrong_or_missing_rights_never_count_as_a_completed_split(self):
        for change in ["root", "outcome", "mint", "count"]:
            h = host(); d = draft(h); h.run("pepper_split", confirmed(d))
            data = h.responses["get_ticket_split"]
            if change == "root": data["rootCollateralId"] = "other"
            if change == "outcome": data["assets"][0]["condition"]["outcomeId"] = "other"
            if change == "mint": data["assets"][0]["nft"]["mintStatus"] = "pending"
            if change == "count": data["assets"].pop()
            self.assertFalse(h.run("pepper_parlay_status")["ok"])

    def test_other_account_cannot_restore_or_confirm_a_saved_draft(self):
        h = host(); d = draft(h)
        h.responses["get_connection_permissions"]["accountId"] = "other"
        self.assertIsNone(h.run("pepper_parlay_status")["draft"])
        self.assertEqual(h.run("pepper_split", confirmed(d))["code"], "PARLAY_DRAFT_REQUIRED")
        self.assertNotIn("create_ticket_split", h.call_names())

    def test_wallet_action_or_ownership_loss_refuses_listing(self):
        for change in ["owner", "actions"]:
            h = host(); d = draft(h); h.run("pepper_split", confirmed(d))
            asset = h.responses["get_ticket_split"]["assets"][1]
            if change == "owner": asset["isOwner"] = False
            if change == "actions": asset["nft"]["actions"] = []
            self.assertEqual(h.run("pepper_offer", {"draftId": d["draftId"], "askingPrice": "25"})["code"], "LISTING_UNAVAILABLE")

    def test_failed_presave_prevents_any_issuance(self):
        h = host(); d = draft(h)
        h.state.set = lambda *_: (_ for _ in ()).throw(OSError("disk error"))
        self.assertEqual(h.run("pepper_split", confirmed(d))["code"], "STATE_UNAVAILABLE")
        self.assertNotIn("create_ticket_split", h.call_names())

    def test_native_approval_denial_does_not_retry(self):
        h = host(); d = draft(h)
        h.responses["create_ticket_split"] = {"ok": False, "error": "The user did not approve running write-capable MCP tool 'create_ticket_split' on untrusted server 'parlaytix-prod'. The command was NOT run. Do not retry without explicit user direction."}
        self.assertEqual(h.run("pepper_split", confirmed(d))["code"], "APPROVAL_DENIED")
        self.assertEqual(h.run("pepper_parlay_status")["draft"]["phase"], "preview")
        self.assertEqual(h.call_names().count("create_ticket_split"), 1)

    def test_a_definite_refusal_names_its_reason_and_leaves_a_usable_preview(self):
        for error in ("TICKET_ALREADY_FRACTIONALIZED (HTTP 409)",
                      "Error executing tool create_ticket_split: CONDITION_RESOLVES_TOO_SOON (HTTP 409)"):
            h = host(); d = draft(h)
            h.responses["create_ticket_split"] = {"ok": False, "error": error}
            refused = h.run("pepper_split", confirmed(d))
            self.assertIn(refused["code"], error)
            self.assertNotIn("Error executing", refused["error"])
            self.assertFalse(refused["retryAutomatically"])
            self.assertEqual(h.run("pepper_parlay_status")["draft"]["phase"], "preview")
            self.assertTrue(h.run("pepper_parlay_forget", {"draftId": d["draftId"], "confirmed": True})["ok"])
            self.assertTrue(h.run("pepper_parlay", {"collateralId": "tc_owned", "eventId": "123"})["ok"])

    def test_only_exact_listed_codes_are_named(self):
        for error in ("SECRET_TOKEN_LEAK (HTTP 409)", "TICKET_ALREADY_FRACTIONALIZED", "ticket_already_fractionalized (HTTP 409)",
                      "TICKET_ALREADY_FRACTIONALIZED (HTTP 200)", "XTICKET_NOT_FOUND (HTTP 404)"):
            h = host(); d = draft(h)
            h.responses["create_ticket_split"] = {"ok": False, "error": error}
            refused = h.run("pepper_split", confirmed(d))
            self.assertEqual(refused["code"], "RESULT_UNKNOWN", error)
            self.assertNotIn("SECRET", refused["error"])
            self.assertEqual(h.state.values[next(iter(h.state.values))]["phase"], "requesting")

    def test_a_request_parlaytix_never_recorded_returns_to_preview_with_the_same_key(self):
        h = host(); d = draft(h)
        h.responses["create_ticket_split"] = RuntimeError("synthetic response loss")
        self.assertEqual(h.run("pepper_split", confirmed(d))["code"], "RESULT_UNKNOWN")
        h.responses["get_ticket_split"] = {"ok": False, "error": "TICKET_SPLIT_NOT_FOUND (HTTP 404)"}
        status = h.run("pepper_parlay_status")
        self.assertTrue(status["notCreated"])
        self.assertEqual(status["draft"]["phase"], "preview")
        h.responses["create_ticket_split"] = {"splitId": "split_1"}
        self.assertTrue(h.run("pepper_split", confirmed(d))["ok"])
        keys = [args["idempotencyKey"] for _, tool, args in h.calls if tool == "create_ticket_split"]
        self.assertEqual(keys, ["pepper-" + d["draftId"]] * 2)

    def test_a_known_split_that_goes_missing_stays_protected(self):
        h = host(); d = draft(h)
        self.assertTrue(h.run("pepper_split", confirmed(d))["ok"])
        h.responses["get_ticket_split"] = {"ok": False, "error": "TICKET_SPLIT_NOT_FOUND (HTTP 404)"}
        self.assertEqual(h.run("pepper_parlay_status")["code"], "TICKET_SPLIT_NOT_FOUND")
        self.assertEqual(h.run("pepper_parlay_forget", {"draftId": d["draftId"], "confirmed": True})["code"], "PARLAY_PENDING")

    def test_a_failed_split_can_be_forgotten(self):
        h = host(); d = draft(h)
        h.responses["get_ticket_split"] = {**h.responses["get_ticket_split"], "splitCompleted": False, "status": "failed"}
        h.run("pepper_split", confirmed(d))
        self.assertIn("failed", h.run("pepper_parlay_status")["nextStep"])
        self.assertTrue(h.run("pepper_parlay_forget", {"draftId": d["draftId"], "confirmed": True})["ok"])


if __name__ == "__main__":
    unittest.main()
