"""A new account with no ticket must never dead-end. Synthetic responses only."""
import unittest

from test_plugin import Host, asset


def host(inventory=None):
    return Host({"list_my_ticket_inventory": {"items": inventory or [], "page": 1, "hasMore": False,
                                              **({} if inventory else {"emptyReason": "NO_VERIFIED_TICKET"})},
                 "list_my_ticket_assets": {"items": []},
                 "search_ticket_listings": {"items": [asset()], "page": 1, "hasMore": False},
                 "search_ticket_events": {"items": []}})


def product_calls(h):
    return [(tool, args) for _, tool, args in h.calls if tool != "get_connection_permissions"]


class NoTicketTests(unittest.TestCase):
    def test_start_says_what_pepper_can_do_without_any_product_call(self):
        h = host()
        result = h.run("pepper_start")
        self.assertEqual([choice["nextTool"] for choice in result["choices"]], ["pepper_owned", "pepper_find"])
        self.assertGreaterEqual(len(result["whatPepperCanDo"]), 4)
        self.assertTrue(all(item["say"] and item["does"] and item["tool"].startswith("pepper_") for item in result["whatPepperCanDo"]))
        self.assertEqual(h.calls, [])

    def test_find_without_a_query_browses_live_fan_tickets(self):
        for args in ({}, {"query": ""}, {"page": 2}):
            h = host()
            result = h.run("pepper_find", args)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["browse"])
            self.assertEqual(len(result["listings"]["items"]), 1)
            self.assertEqual(product_calls(h), [("search_ticket_listings", {"query": "", "page": args.get("page", 1)})])

    def test_a_named_search_still_needs_three_characters_and_reads_events(self):
        h = host()
        for query in (" ", "ab", "x" * 121):
            self.assertFalse(h.run("pepper_find", {"query": query})["ok"], query)
        self.assertEqual(product_calls(h), [])
        result = h.run("pepper_find", {"query": "Home"})
        self.assertNotIn("browse", result)
        self.assertEqual([tool for tool, _ in product_calls(h)], ["search_ticket_events", "search_ticket_listings"])

    def test_an_empty_inventory_points_on_instead_of_stopping(self):
        empty = host().run("pepper_owned")
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["wholeTickets"]["emptyReason"], "NO_VERIFIED_TICKET")
        self.assertIn("pepper_find with no query", empty["nextStep"])
        held = host([{"collateralId": "tc_1", "eligibleForSplit": True, "canSplit": False}]).run("pepper_owned")
        self.assertNotIn("nextStep", held)

    def test_an_empty_shop_is_reported_as_empty_not_as_an_error(self):
        h = host()
        h.responses["search_ticket_listings"] = {"items": [], "page": 1, "hasMore": False}
        result = h.run("pepper_find")
        self.assertTrue(result["ok"])
        self.assertEqual(result["listings"]["items"], [])
        self.assertIn("nothing is listed right now", result["guidance"])

    def test_listing_rows_are_short_and_carry_only_what_a_fan_needs_to_choose(self):
        h = host()
        item = h.responses["search_ticket_listings"]["items"][0]
        item["ticket"].update(faceValue=153, venue="Arena")
        item["pageUrl"] = "https://parlaytix.xyz/claims/claim-1"
        item["nft"]["transactionHash"] = "0x" + "f" * 64
        row = h.run("pepper_find")["listings"]["items"][0]
        self.assertEqual(row, {"claimId": "claim-1", "eventName": "Home vs Away", "eventDate": "2026-12-01T20:00:00Z", "timeZone": None,
                               "venue": "Arena", "section": "101", "row": "5", "seatPrice": 153, "pick": "Visitor wins",
                               "decidingGame": "Earlier Home vs Visitor", "decidingGameStart": None, "price": 20.17, "currency": "USD",
                               "chance": 0.31, "tradingClosesAt": "2026-11-28T20:00:00Z", "pageUrl": "https://parlaytix.xyz/claims/claim-1"})
        item["pageUrl"] = "https://evil.example/claims/claim-1"
        item["pricing"]["freshness"] = "stale"
        row = h.run("pepper_find", {"query": "Home"})["listings"]["items"][0]
        self.assertIsNone(row["pageUrl"])
        self.assertIsNone(row["chance"])

    def test_a_named_search_still_answers_when_the_event_lookup_does_not(self):
        blocked = "MCP tool 'search_ticket_events' on untrusted server 'parlaytix-prod' was blocked: the approval system was unavailable (fail-closed)."
        for failure in ({"ok": False, "error": blocked}, RuntimeError("provider timeout"), {"ok": True, "result": "x", "truncated": True}):
            h = host()
            h.responses["search_ticket_events"] = failure
            result = h.run("pepper_find", {"query": "Home"})
            self.assertTrue(result["ok"], result)
            self.assertIsNone(result["events"])
            self.assertTrue(result["eventsUnavailable"])
            self.assertEqual(len(result["listings"]["items"]), 1)
            self.assertIn("did not return", result["guidance"])
        h = host()
        h.responses["search_ticket_events"] = {"ok": False, "error": "MCP server 'parlaytix-prod' requires re-authentication."}
        self.assertEqual(h.run("pepper_find", {"query": "Home"})["code"], "NEEDS_REAUTH")


if __name__ == "__main__":
    unittest.main()
