"""Pepper's ticket-to-review flow, using Hermes-owned MCP, OAuth and state."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from urllib.parse import quote, urlsplit
from uuid import uuid4


SERVER = "parlaytix-prod"

JOURNEY_PROMPT = """Pepper handles ticket requests inside this existing Hermes conversation.
When the user says 'parlay this', or asks to conditionalize/fractionalize a ticket, call pepper_start.
They do not need to name Pepper, tools, ticket IDs or a deciding game. Ask one short question at a time;
skip details they already supplied. Fractionalizing here means two complementary outcome rights over
one ticket, not percentage shares or two seats.
If they have a ticket, use pepper_owned, match their actual owned ticket and ask which to use if unclear.
Use pepper_questions to offer eligible deciding games; ask what outcome they want to keep. If they need
a ticket, ask the event/date and budget, then use pepper_find and the existing buyer review flow.
Use pepper_parlay to show both rights and the separate attended/deciding events with cancellation rules.
Refresh stale terms. Ask for explicit confirmation of the displayed split and kept outcome before
pepper_split; 'parlay this' alone never confirms issuance. Resume pending work with pepper_parlay_status.
After confirmed issuance, ask whether to list the other side and at what price. pepper_offer supplies
its wallet review page; the user enters the price and confirms there. Verify the actual listing with
pepper_parlay_status. A prepared link is not a listing, and a listing is not a sale.
If nothing fits their budget, offer a Scout watch: pepper_watch saves a draft from the matched event, total cap,
ticket count, deadline and whether to tell them, check first or just buy. Confirm the terms before
pepper_watch_control activates it. Pepper sends no updates itself; read pepper_watches when they ask, and
answer a pending approval with pepper_watch_respond only after they decide. Payment happens on ParlayTix.
Do not invent ownership, odds, verification, outcomes or background work. Respect native approvals."""


def _start(ctx, args):
    return {"ok": True, "stage": "choose_ticket_path",
            "question": "Perfect. Do you already have a ticket you'd like to split into two outcome rights, or would you like to find one?",
            "choices": [{"intent": "owned_ticket", "label": "I have a ticket", "nextTool": "pepper_owned"},
                        {"intent": "find_ticket", "label": "Find a ticket", "nextTool": "pepper_find",
                         "followUp": "Which event would you like to attend, and what's your budget?"}],
            "nextStep": "Ask the opening question unless the user already answered it. Follow their intent one question at a time. Check actual inventory before selecting an owned ticket. Never invent backing or treat this opening as confirmation to split or list."}


class Refusal(Exception):
    """A bounded, public explanation; never contains upstream exception text."""

    def __init__(self, message, code="REQUEST_REFUSED"):
        super().__init__(message)
        self.code = code


def _host_failure(error, tool):
    # These are exact stock Hermes handler templates, not inferred meanings of
    # arbitrary upstream text. call_mcp drops the native needs_reauth metadata.
    denial = (f"The user did not approve running write-capable MCP tool '{tool}' on untrusted server "
              f"'{SERVER}'. The command was NOT run. Do not retry without explicit user direction.")
    unavailable = (f"MCP tool '{tool}' on untrusted server '{SERVER}' was blocked: the approval "
                   "system was unavailable (fail-closed).")
    if error == denial:
        return Refusal("The user denied or cancelled this call. It was not run. Stop; do not retry without explicit user direction.", "APPROVAL_DENIED")
    if error == unavailable:
        return Refusal("Hermes could not obtain approval, so the call was not run. Stop and restore interactive approval before continuing.", "APPROVAL_UNAVAILABLE")
    if isinstance(error, str) and error.startswith(f"MCP server '{SERVER}' requires re-authentication."):
        return Refusal("Stop and ask the user to re-authenticate with `hermes mcp login parlaytix-prod` in the same profile. Do not retry this tool before sign-in completes.", "NEEDS_REAUTH")
    return Refusal("The result is unknown. Do not automatically retry. Inspect ParlayTix status and the Hermes connection before deciding whether to retry.", "RESULT_UNKNOWN")


def _text(value, name, maximum=200):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise Refusal(f"Invalid {name}.")
    return value


def _integer(value, name, maximum=9007199254740991):
    if type(value) is not int or not 1 <= value <= maximum:
        raise Refusal(f"Invalid {name}.")
    return value


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise Refusal("Invalid price.")
    try:
        price = Decimal(str(value))
    except InvalidOperation:
        raise Refusal("Invalid price.") from None
    if not price.is_finite() or price < 0:
        raise Refusal("Invalid price.")
    return price


def _call(ctx, tool, arguments):
    try:
        envelope = ctx.call_mcp(SERVER, tool, arguments, timeout=60)
    except PermissionError:
        raise Refusal("Grant Pepper access to parlaytix-prod in Hermes plugin settings. Do not retry until access is granted.", "MCP_ACCESS_REQUIRED") from None
    except Exception:
        # The host may raise transport errors containing private URLs or credentials.
        raise _host_failure(None, tool) from None
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        raise _host_failure(envelope.get("error") if isinstance(envelope, dict) else None, tool)
    if envelope.get("truncated"):
        raise Refusal("ParlayTix returned an incomplete result. Narrow the search before continuing.")
    payload = envelope.get("structuredContent", envelope.get("result"))
    # Hermes can retain structuredContent, retain structured-only result, or deduplicate
    # FastMCP's identical text/structured bodies into a JSON string under result.
    for _ in range(4):
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                raise _host_failure(None, tool) from None
        if not isinstance(payload, dict):
            raise _host_failure(None, tool)
        if payload.get("isError") or payload.get("truncated") or payload.get("error") is not None:
            raise _host_failure(payload.get("error"), tool)
        if "result" in payload and set(payload) <= {"result", "_meta"}:
            payload = payload["result"]
        else:
            return payload
    raise _host_failure(None, tool)


def _checkout(data, claim_id):
    if data.get("checkoutKind") != "wallet" or data.get("claimId") != claim_id:
        raise Refusal("The review response does not match the selected fan ticket.")
    if data.get("paymentUrl") is not None or data.get("paymentRequestId") is not None:
        raise Refusal("This payment route is not supported by Pepper.")
    expected_path = "/claims/" + quote(claim_id, safe="")
    url = data.get("confirmUrl")
    if not isinstance(url, str):
        raise Refusal("ParlayTix did not return a valid review link.")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise Refusal("ParlayTix did not return a valid review link.") from None
    if (parsed.scheme != "https" or parsed.netloc != "parlaytix.xyz"
            or parsed.path != expected_path or parsed.query or parsed.fragment
            or url != f"https://parlaytix.xyz{expected_path}"):
        raise Refusal("ParlayTix did not return the canonical review link for this card.")
    receipt = data.get("receipt")
    if receipt is not None and (not isinstance(receipt, dict) or receipt.get("claimId") != claim_id
                                or receipt.get("listingId") != data.get("listingId")):
        raise Refusal("The purchase receipt does not match this card.")
    completed = bool(receipt and data.get("status") == "completed" and receipt.get("status") == "completed"
                     and receipt.get("ownershipTransferStatus") == "completed")
    return {"ok": True, "checkout": data, "purchaseCompleted": completed,
            "nextStep": "Purchase receipt confirmed." if completed
            else "Review on ParlayTix. Only a completed receipt proves purchase; a review link does not reserve the card."}


def _connection(ctx, args):
    permissions = _call(ctx, "get_connection_permissions", {})
    _text(permissions.get("accountId"), "account")
    if (not isinstance(permissions.get("scopes"), list)
            or not all(isinstance(scope, str) for scope in permissions["scopes"])):
        raise Refusal("ParlayTix did not return connection permissions.")
    return {"ok": True, "connection": permissions}


def _account(ctx, scope="ticketing:read"):
    permissions = _connection(ctx, {})["connection"]
    if scope not in permissions["scopes"]:
        raise Refusal("The current ParlayTix connection lacks the required ticket permission. Update consent before continuing.", "SCOPE_REQUIRED")
    return permissions["accountId"]


def _same_account(ctx, account, scope="ticketing:read"):
    if _account(ctx, scope) != account:
        raise Refusal("The ParlayTix account changed during this request. Start again in the intended account; no saved ticket is being shown.", "ACCOUNT_CHANGED")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fields(source, names):
    """Bounded display fields only: no raw wallet, owner, payment or URL payloads."""
    source = source if isinstance(source, dict) else {}
    return {name: source.get(name) if isinstance(source.get(name), str)
            and source[name].strip() and len(source[name]) <= 8000 else None for name in names}


def _card(asset):
    if not isinstance(asset, dict):
        raise Refusal("ParlayTix did not return a valid fan ticket.")
    claim_id = _text(asset.get("claimId"), "claimId")
    ticket = _fields(asset.get("ticket"), (
        "collateralId", "canonicalEventKey", "eventName", "eventDate", "timeZone", "venue", "city",
        "section", "row", "seat", "backingClass"))
    quantity = (asset.get("ticket") or {}).get("quantity") if isinstance(asset.get("ticket"), dict) else None
    ticket["quantity"] = quantity if type(quantity) is int and quantity > 0 else None
    condition = _fields(asset.get("condition"), (
        "kind", "description", "eventTitle", "outcomeLabel", "outcome", "sourceType",
        "outcomeId", "polymarketEventId", "polymarketConditionId", "polymarketTokenId",
        "marketTicker", "eventTicker", "resultKeySchema", "cellKey", "rowLegKey", "columnLegKey",
        "resolutionRules", "expectedResolutionDate", "gameStartTime"))
    nft = _fields(asset.get("nft"), ("termsHash", "cardAddress", "tokenId"))
    nft_source = asset.get("nft")
    if not isinstance(nft_source, dict):
        nft_source = {}
    nft["chainId"] = nft_source.get("chainId") if type(nft_source.get("chainId")) is int else None
    listed = asset.get("listing")
    listing = None
    if isinstance(listed, dict):
        listing = {**_fields(listed, ("status", "expiresAt")),
                   "listingId": _text(listed.get("listingId"), "listingId"),
                   "version": _integer(listed.get("version"), "listing version"),
                   "price": str(_price(listed.get("price"))),
                   "currency": _text(listed.get("currency"), "currency", 10)}
    pricing = asset.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    probability = pricing.get("probability")
    if (not isinstance(probability, (int, float)) or isinstance(probability, bool) or not 0 <= probability <= 1
            or pricing.get("freshness") != "fresh" or not pricing.get("observedAt")):
        probability = None
    deadlines = _fields(asset, ("tradingClosesAt", "claimBy"))
    missing = [name for name, value in (
        ("ticket.eventName", ticket["eventName"]), ("ticket.eventDate", ticket["eventDate"]),
        ("condition.eventTitle", condition["eventTitle"]), ("condition.outcomeLabel", condition["outcomeLabel"]),
        ("tradingClosesAt", deadlines["tradingClosesAt"])) if not value]
    unavailable = None
    if asset.get("isOwner") is True:
        unavailable = "OWN_LISTING"
    elif asset.get("tradingClosed") is True:
        unavailable = "TRADING_CLOSED"
    elif nft["chainId"] != 8453:
        unavailable = "UNSUPPORTED_CHAIN"
    elif (not listing or listing["status"] != "live" or ticket["backingClass"] != "live"
          or asset.get("isOwner") is not False or asset.get("tradingClosed") is not False
          or not isinstance(nft_source.get("actions"), list) or "buy" not in nft_source["actions"]):
        unavailable = "CARD_UNAVAILABLE"
    elif missing:
        unavailable = "MISSING_TERMS"
    return {"claimId": claim_id, "ticket": ticket, "condition": condition, "listing": listing, "nft": nft,
            **deadlines, "checkedAt": _now(),
            "pricing": {**_fields(pricing, ("freshness", "source", "probabilityBasis", "observedAt")),
                        "probability": probability},
            "availability": {"canReview": unavailable is None, "reason": unavailable, "missingFields": missing},
            "outcomes": {"wins": "The card can entitle its holder to the seat under its published terms.",
                         "loses": "No seat from this card; the premium is spent.",
                         "voidOrCancelled": condition["resolutionRules"] or "Rules unavailable. Check ParlayTix before deciding."}}


def _asset(ctx, claim_id):
    asset = _call(ctx, "get_ticket_asset", {"claimId": claim_id}).get("asset")
    card = _card(asset)
    if card["claimId"] != claim_id:
        raise Refusal("The refreshed card does not match the selection.")
    return card


def _terms(card):
    # Observations and availability may change without changing the accepted deal.
    listing = card["listing"]
    return {"claimId": card["claimId"], "ticket": card["ticket"], "condition": card["condition"],
            "nft": card["nft"], "tradingClosesAt": card["tradingClosesAt"], "claimBy": card["claimBy"],
            "listing": {**{k: listing[k] for k in ("listingId", "version", "currency", "expiresAt")},
                        "price": _price(listing["price"])} if listing else None}


def _state(ctx, account, value=None, *, write=False):
    key = "selection:" + sha256(account.encode("utf-8")).hexdigest()
    try:
        if write:
            ctx.state.set(key, value)
            return value
        return ctx.state.get(key)
    except Exception:
        raise Refusal("Pepper could not read or save this profile's selection. Existing data was not intentionally cleared. Resolve the Hermes storage error before continuing.", "STATE_UNAVAILABLE") from None


def _saved(ctx, account):
    saved = _state(ctx, account)
    if saved is None:
        return None
    try:
        valid = (type(saved["schemaVersion"]) is int and saved["schemaVersion"] == 1 and saved["accountId"] == account
                 and re.fullmatch(r"[0-9a-f]{32}", saved["selectionId"])
                 and isinstance(saved["selectedAt"], str) and _terms(saved["card"]))
    except (TypeError, KeyError, Refusal):
        valid = False
    if not valid:
        raise Refusal("The saved selection cannot be read safely. It has been preserved. Select the ticket again or explicitly forget it.", "STATE_INVALID")
    return saved


def _selection(saved):
    return {key: saved[key] for key in ("selectionId", "selectedAt", "card")}


def _select(ctx, args):
    claim_id = _text(args.get("claimId"), "claimId")
    account = _account(ctx)
    card = _asset(ctx, claim_id)
    _same_account(ctx, account)
    saved = {"schemaVersion": 1, "accountId": account, "selectionId": uuid4().hex,
             "selectedAt": _now(), "card": card}
    _state(ctx, account, saved, write=True)
    return {"ok": True, "selection": _selection(saved),
            "nextStep": "Explain the attended event, separate deciding outcome and both results. Ask for acceptance before review. Saving does not reserve or buy the ticket."}


def _resume(ctx, args):
    account = _account(ctx)
    saved = _saved(ctx, account)
    if saved is None:
        return {"ok": True, "selection": None, "nextStep": "No saved selection for this account and profile. Find and select a ticket."}
    card = _asset(ctx, saved["card"]["claimId"])
    status = _status(ctx, {"claimId": card["claimId"]}) if card["nft"]["chainId"] == 8453 else None
    _same_account(ctx, account)
    changed = _terms(card) != _terms(saved["card"])
    return {"ok": True, "selection": _selection(saved), "currentCard": card, "termsChanged": changed,
            "status": status, "nextStep": "Terms changed. Select the ticket again and obtain fresh acceptance before review." if changed
            else "These are freshly checked terms and status. Resuming never repeats review or wallet confirmation."}


def _forget(ctx, args):
    account = _account(ctx)
    _state(ctx, account, None, write=True)
    return {"ok": True, "nextStep": "Saved selection cleared for this account and profile. This does not cancel a wallet operation or revoke ParlayTix consent."}


def _find(ctx, args):
    query = _text(args.get("query"), "query", 120)
    if len(query) < 3:
        raise Refusal("Use an explicit event query of at least three characters.")
    page = _integer(args.get("page", 1), "page", 1000)
    events = _call(ctx, "search_ticket_events", {"query": query})
    listings = _call(ctx, "search_ticket_listings", {"query": query, "page": page})
    if not isinstance(listings.get("items"), list):
        raise Refusal("ParlayTix did not return a valid listing page.")
    return {"ok": True, "events": events, "listings": listings,
            "guidance": "Match the attended event, date and seat explicitly. Listing matches are candidates, not guaranteed matches. Fan tickets depend on a separate outcome; ask whether conditional attendance is acceptable."}


def _review(ctx, args):
    if args.get("conditionalTicketAccepted") is not True:
        raise Refusal("Explain both outcomes and ask the user to accept the conditional fan ticket before review.")
    selection_id = _text(args.get("selectionId"), "selectionId")
    account = _account(ctx, "ticketing:acquire")
    saved = _saved(ctx, account)
    if saved is None or saved["selectionId"] != selection_id:
        raise Refusal("Select the ticket in this account first. The saved selection does not match this request.", "SELECTION_REQUIRED")
    claim_id = saved["card"]["claimId"]
    card = _asset(ctx, claim_id)
    if _terms(card) != _terms(saved["card"]):
        raise Refusal("The ticket or listing terms changed. Select it again and obtain fresh user acceptance.", "TERMS_CHANGED")
    if not card["availability"]["canReview"]:
        raise Refusal("This card is not currently ready for review. Inspect its availability and missing terms before continuing.", card["availability"]["reason"])
    listing = card["listing"]
    listing_id, version = listing["listingId"], listing["version"]
    price = _price(listing["price"])
    data = _call(ctx, "create_ticket_checkout", {
        "listingId": listing_id, "expectedListingVersion": version,
        "idempotencyKey": "pepper-review-" + selection_id,
    })
    if (data.get("listingId") != listing_id or type(data.get("listingVersion")) is not int
            or data["listingVersion"] != version or _price(data.get("askingPrice")) != price
            or _price(data.get("priceUSD")) != price):
        raise Refusal("The review terms changed. Refresh the offer and obtain fresh user acceptance.")
    result = _checkout(data, claim_id)
    _same_account(ctx, account, "ticketing:acquire")
    return {**result, "card": card, "selectionId": selection_id, "currency": listing["currency"]}


def _status(ctx, args):
    claim_id = _text(args.get("claimId"), "claimId")
    return _checkout(_call(ctx, "get_ticket_checkout", {"claimId": claim_id}), claim_id)


def _parlay_state(ctx, account, value=None, *, write=False):
    key = "parlay:" + sha256(account.encode("utf-8")).hexdigest()
    try:
        if write:
            ctx.state.set(key, value)
            return value
        saved = ctx.state.get(key)
    except Exception:
        raise Refusal("Pepper could not save or restore the Parlay draft. Stop before creating any rights.", "STATE_UNAVAILABLE") from None
    if saved is not None:
        try:
            valid = (isinstance(saved, dict) and saved["schemaVersion"] == 1 and saved["accountId"] == account
                     and re.fullmatch(r"[0-9a-f]{32}", saved["draftId"]) and isinstance(saved["phase"], str)
                     and _text(saved["source"]["collateralId"], "collateralId") and _text(saved["source"]["eventId"], "eventId")
                     and len(saved["preview"]["outcomes"]) == 2 and _split_terms(saved["preview"]))
        except (KeyError, TypeError, AttributeError, Refusal):
            valid = False
        if not valid:
            raise Refusal("The saved Parlay draft is unreadable and has been preserved.", "STATE_INVALID")
    return saved


def _holder_preview(data):
    admission = data.get("admission", {})
    outcomes = data.get("outcomes")
    if (data.get("splitKind") != "complementary_ticket_rights" or data.get("createsListings") is not False
            or not isinstance(admission, dict) or admission.get("eligible") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", str(admission.get("universeHash", "")))
            or not re.fullmatch(r"0x[0-9a-f]{64}", str(data.get("previewHash", "")))
            or not isinstance(outcomes, list) or len(outcomes) != 2
            or {o.get("outcome") for o in outcomes if isinstance(o, dict)} != {"YES", "NO"}):
        raise Refusal("ParlayTix did not return an eligible complementary ticket split.", "SPLIT_UNAVAILABLE")
    sides = []
    for outcome in outcomes:
        sides.append({**_fields(outcome, ("outcome", "label", "description", "resolutionRules", "resolutionAt", "resolutionTimeKind", "observedAt", "freshness")),
                      "outcomeId": _text(outcome.get("outcomeId"), "outcomeId"),
                      "probability": outcome.get("probability") if outcome.get("freshness") == "fresh"
                      and type(outcome.get("probability")) in (int, float) and 0 <= outcome["probability"] <= 1 else None})
    if len({side["outcomeId"] for side in sides}) != 2 or any(not side["resolutionRules"] or not side["resolutionAt"] for side in sides):
        raise Refusal("The split needs distinct outcomes with source rules and resolution timing.", "SPLIT_UNAVAILABLE")
    ticket = _fields(data.get("ticket"), ("collateralId", "canonicalEventKey", "eventName", "eventDate", "venue", "section", "row", "seat", "backingClass"))
    if ticket["collateralId"] != data.get("collateralId") or ticket["backingClass"] != "live":
        raise Refusal("The preview does not identify a verified whole ticket.", "SPLIT_UNAVAILABLE")
    return {"ticket": ticket, "outcomes": sides, "universeHash": admission["universeHash"], "previewHash": data["previewHash"],
            "splitEnabled": data.get("splitEnabled") is True}


def _split_terms(preview):
    return {**{k: v for k, v in preview.items() if k != "splitEnabled"}, "outcomes": [{k: v for k, v in side.items() if k not in ("probability", "observedAt", "freshness")}
                                    for side in preview["outcomes"]]}


def _parlay_draft(saved):
    return {key: saved.get(key) for key in ("draftId", "createdAt", "phase", "preview", "keepOutcomeId", "offer", "splitId")}


def _owned(ctx, args):
    account = _account(ctx)
    page = _integer(args.get("page", 1), "page", 1000)
    whole = _call(ctx, "list_my_ticket_inventory", {"page": page})
    held = _call(ctx, "list_my_ticket_assets", {"page": page, "view": "held"})
    _same_account(ctx, account)
    return {"ok": True, "wholeTickets": whole, "fanTickets": held,
            "guidance": "A whole ticket with eligibleForSplit=true can be previewed. canSplit and the refreshed preview's splitEnabled control whether creation is currently available. Existing fan tickets cannot be re-split. Never substitute company inventory or invent verified backing."}


def _questions(ctx, args):
    account = _account(ctx)
    body = {"collateralId": _text(args.get("collateralId"), "collateralId"), "page": _integer(args.get("page", 1), "page", 1000)}
    if args.get("query"):
        body["query"] = _text(args["query"], "query", 120)
    if args.get("eventId"):
        body["eventId"] = _text(args["eventId"], "eventId")
        body.pop("page", None)
        body.pop("query", None)
    result = _call(ctx, "search_ticket_split_questions", body)
    _same_account(ctx, account)
    return {"ok": True, "questions": result, "nextStep": "Choose a binary question. Use its exact eventId and conditionId, when supplied, to preview the split."}


def _parlay(ctx, args):
    account = _account(ctx)
    prior = _parlay_state(ctx, account)
    if prior and prior.get("phase") != "preview":
        raise Refusal("An existing Parlay request needs attention. Read pepper_parlay_status before starting another.", "PARLAY_PENDING")
    source = {"collateralId": _text(args.get("collateralId"), "collateralId"), "eventId": _text(args.get("eventId"), "eventId")}
    if args.get("conditionId"):
        source["conditionId"] = _text(args["conditionId"], "conditionId")
    preview = _holder_preview(_call(ctx, "preview_ticket_split", source))
    if preview["ticket"]["collateralId"] != source["collateralId"]:
        raise Refusal("The preview does not match the chosen ticket.")
    _same_account(ctx, account)
    saved = {"schemaVersion": 1, "accountId": account, "draftId": uuid4().hex, "createdAt": _now(),
             "phase": "preview", "source": source, "preview": preview}
    _parlay_state(ctx, account, saved, write=True)
    return {"ok": True, "draft": _parlay_draft(saved), "splitCompleted": False,
            "nextStep": "Explain the attended event and separate deciding question, both rights and the source's cancellation/ambiguous-result rules. Ask which outcome to keep and whether to create this exact split. This is not a cash parlay or a ticket-price forecast. Both rights initially remain yours; listing needs a separate wallet confirmation."}


def _parlay_status_for(ctx, account, saved):
    query = {"splitId": saved["splitId"]} if saved.get("splitId") else {"idempotencyKey": "pepper-" + saved["draftId"]}
    data = _call(ctx, "get_ticket_split", query)
    split_id = _text(data.get("splitId"), "splitId")
    if (saved.get("splitId") and saved["splitId"] != split_id) or data.get("rootCollateralId") != saved["source"]["collateralId"]:
        raise Refusal("The returned split does not match this saved ticket.")
    assets = data.get("assets")
    if (not isinstance(assets, list) or any(not isinstance(asset, dict) or not isinstance(asset.get("condition"), dict)
                                          or not isinstance(asset.get("nft"), dict) for asset in assets)):
        raise Refusal("ParlayTix did not return split assets.")
    expected = {side["outcomeId"] for side in saved["preview"]["outcomes"]}
    ready = data.get("splitCompleted") is True
    if ready and (len(assets) != 2 or {asset.get("condition", {}).get("outcomeId") for asset in assets} != expected
                  or len({asset.get("claimId") for asset in assets}) != 2
                  or any(asset.get("rootCollateralId") != saved["source"]["collateralId"] or asset.get("nft", {}).get("mintStatus") != "minted" for asset in assets)):
        raise Refusal("The confirmed rights do not match the accepted split.", "SPLIT_MISMATCH")
    _same_account(ctx, account)
    saved = {**saved, "splitId": split_id, "phase": "ready" if ready else data.get("status", "pending")}
    _parlay_state(ctx, account, saved, write=True)
    offer = saved.get("offer")
    offered = next((asset for asset in assets if offer and asset.get("claimId") == offer["claimId"]), None)
    listing = offered.get("listing") if offered else None
    listing_live = bool(listing and listing.get("status") == "live")
    listing_matches = False
    if listing_live and offered is not None and offer is not None and listing is not None:
        listing_matches = (offered.get("nft", {}).get("termsHash") == offer["termsHash"]
                           and _price(listing.get("price")) == _price(offer["askingPrice"]))
    return {"ok": True, "draft": _parlay_draft(saved), "splitCompleted": ready, "status": data,
            "listingObserved": listing_live, "listingMatchesDraft": listing_matches,
            "nextStep": "Both rights are confirmed. Keep the chosen outcome; use pepper_offer to review the other right's proposed asking price. A live listing is not a sale." if ready
            else "Issuance is not confirmed. Read status later; do not create another split or list a pending right."}


def _split(ctx, args):
    account = _account(ctx, "ticketing:write")
    saved = _parlay_state(ctx, account)
    if not saved or saved["draftId"] != args.get("draftId"):
        raise Refusal("Preview the exact split in this account before confirming it.", "PARLAY_DRAFT_REQUIRED")
    if saved.get("phase") != "preview":
        return _parlay_status_for(ctx, account, saved)
    keep = _text(args.get("keepOutcomeId"), "keepOutcomeId")
    if args.get("confirmed") is not True or keep not in {side["outcomeId"] for side in saved["preview"]["outcomes"]}:
        raise Refusal("The user must accept the displayed split and choose the exact outcome to keep.", "SPLIT_CONFIRMATION_REQUIRED")
    current = _holder_preview(_call(ctx, "preview_ticket_split", saved["source"]))
    if not current["splitEnabled"]:
        raise Refusal("Holder splitting is not enabled on ParlayTix yet. Keep this preview; no split was submitted.", "SPLIT_UNAVAILABLE")
    if _split_terms(current) != _split_terms(saved["preview"]):
        raise Refusal("The split terms changed. Preview again and obtain fresh acceptance.", "TERMS_CHANGED")
    _same_account(ctx, account, "ticketing:write")
    saved = {**saved, "phase": "requesting", "keepOutcomeId": keep}
    # Persist the request key BEFORE the write. A lost response is recovered by a read, never a blind retry.
    _parlay_state(ctx, account, saved, write=True)
    try:
        result = _call(ctx, "create_ticket_split", {**saved["source"], "universeHash": current["universeHash"],
                       "previewHash": current["previewHash"], "idempotencyKey": "pepper-" + saved["draftId"], "confirmed": True})
    except Refusal as error:
        if error.code in ("APPROVAL_DENIED", "APPROVAL_UNAVAILABLE"):
            _parlay_state(ctx, account, {**saved, "phase": "preview"}, write=True)
        raise
    saved = {**saved, "splitId": _text(result.get("splitId"), "splitId"), "phase": "pending"}
    _same_account(ctx, account, "ticketing:write")
    _parlay_state(ctx, account, saved, write=True)
    return {"ok": True, "draft": _parlay_draft(saved), "splitCompleted": False,
            "nextStep": "The split was accepted for issuance. Read pepper_parlay_status until both rights are confirmed. Nothing has been listed."}


def _parlay_status(ctx, args):
    account = _account(ctx)
    saved = _parlay_state(ctx, account)
    if not saved:
        return {"ok": True, "draft": None, "nextStep": "No Parlay draft for this account. Start with pepper_owned."}
    if saved.get("phase") == "preview":
        current = _holder_preview(_call(ctx, "preview_ticket_split", saved["source"]))
        _same_account(ctx, account)
        return {"ok": True, "draft": _parlay_draft(saved), "currentPreview": current,
                "termsChanged": _split_terms(current) != _split_terms(saved["preview"]), "splitCompleted": False}
    return _parlay_status_for(ctx, account, saved)


def _offer(ctx, args):
    account = _account(ctx)
    saved = _parlay_state(ctx, account)
    if not saved or saved["draftId"] != args.get("draftId") or not saved.get("keepOutcomeId"):
        raise Refusal("Choose and create a Parlay split before reviewing an offer.", "PARLAY_DRAFT_REQUIRED")
    price = _price(args.get("askingPrice"))
    if price <= 0 or price > 1000000 or price * 100 != (price * 100).to_integral_value():
        raise Refusal("Choose a positive asking price with at most two decimal places.")
    result = _parlay_status_for(ctx, account, saved)
    if not result["splitCompleted"]:
        raise Refusal("Wait until both rights are confirmed before reviewing a listing.", "SPLIT_PENDING")
    asset = next(asset for asset in result["status"]["assets"] if asset["condition"]["outcomeId"] != saved["keepOutcomeId"])
    if asset.get("isOwner") is not True or "list" not in asset.get("nft", {}).get("actions", []):
        raise Refusal("Your wallet cannot currently list the other right. Inspect its actual status.", "LISTING_UNAVAILABLE")
    claim_id = _text(asset.get("claimId"), "claimId")
    offer = {"claimId": claim_id, "askingPrice": str(price), "termsHash": _text(asset.get("nft", {}).get("termsHash"), "termsHash"),
             "reviewUrl": "https://parlaytix.xyz/claims/" + quote(claim_id, safe="")}
    saved = _parlay_state(ctx, account)
    if not saved or saved["draftId"] != args["draftId"]:
        raise Refusal("The active Parlay draft changed. Resume it before reviewing an offer.", "PARLAY_DRAFT_REQUIRED")
    _parlay_state(ctx, account, {**saved, "offer": offer}, write=True)
    return {"ok": True, "offer": offer, "keptOutcomeId": saved["keepOutcomeId"], "listingCreated": False,
            "nextStep": "Open this right on ParlayTix, enter this asking price, review the displayed fee and proceeds, and confirm listing in your wallet. The link does not prefill a price or sign anything. Then use pepper_parlay_status to verify the actual listing."}


def _parlay_forget(ctx, args):
    account = _account(ctx)
    saved = _parlay_state(ctx, account)
    if not saved or saved["draftId"] != args.get("draftId") or args.get("confirmed") is not True:
        raise Refusal("Confirm the exact draft to clear from this profile.")
    if saved.get("phase") not in ("preview", "ready"):
        raise Refusal("Keep the recovery record until the pending split is resolved.", "PARLAY_PENDING")
    _parlay_state(ctx, account, None, write=True)
    return {"ok": True, "nextStep": "The local Parlay draft was cleared. Existing rights, listings and wallet operations remain on ParlayTix."}


WATCH_ACTIONS = {"activate": "activate_scout_intent", "pause": "pause_scout_intent",
                 "resume": "resume_scout_intent", "cancel": "cancel_scout_intent"}
WATCH_NOTE = ("This is stored watch state: monitoring health is unknown, and Pepper sends no updates on its own, "
              "so the user asks again later. A found, approved or filled watch is not ticket delivery.")


def _watch_account(ctx, scope):
    permissions = _connection(ctx, {})["connection"]
    if scope not in permissions["scopes"]:
        raise Refusal("This ParlayTix connection has no Scout watch consent for this step. The user grants it under AI agents in ParlayTix. Do not retry before then.", "WATCH_CONSENT_REQUIRED")
    return permissions["accountId"]


def _watch_same(ctx, account):
    if _connection(ctx, {})["connection"]["accountId"] != account:
        raise Refusal("The ParlayTix account changed during this request. Read pepper_watches in the intended account before doing anything else.", "ACCOUNT_CHANGED")


def _scout_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"scout_[0-9a-f]{32}", value):
        raise Refusal("Use the exact scoutId returned by pepper_watch or pepper_watches.")
    return value


def _usd(value, name):
    price = _price(value)
    if price <= 0 or price > 1000000 or price * 100 != (price * 100).to_integral_value():
        raise Refusal(f"{name} must be a positive USD total with at most two decimal places.")
    return f"{price:.2f}"


def _watch_reply(data, done):
    intent = data.get("intent")
    if not isinstance(intent, dict) or not isinstance(intent.get("scoutId"), str):
        raise _host_failure(None, "scout watch")
    payment = data.get("payment")
    if isinstance(payment, dict):
        # The owner pays on ParlayTix's own payment page. Any other link is dropped, never shown.
        url = payment.get("paymentUrl")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
        except ValueError:
            parsed = None
        trusted = bool(parsed and parsed.scheme == "https" and parsed.netloc in ("parlaytix.xyz", "www.parlaytix.xyz")
                       and parsed.path.startswith("/pay/"))
        payment = {**_fields(payment, ("paymentRequestId", "status")), "paymentUrl": url if trusted else None}
        done = ("Payment is required before this takes effect. " + ("Present paymentUrl as 'Pay on ParlayTix'." if trusted
                else "Ask the user to open this watch in ParlayTix to pay.") + " Pepper never pays, and nothing is bought until the owner confirms there.")
    else:
        payment = None
    return {"ok": True, "watch": intent, "payment": payment, "replayed": data.get("replayed") is True,
            "nextStep": done + " " + WATCH_NOTE}


def _watches(ctx, args):
    account = _watch_account(ctx, "scout-intents:read")
    if args.get("scoutId"):
        data = _call(ctx, "get_scout_intent", {"scoutId": _scout_id(args["scoutId"])})
    else:
        body = {"limit": 20}
        if args.get("cursor"):
            body["cursor"] = _text(args["cursor"], "cursor", 20)
        data = _call(ctx, "list_scout_intents", body)
    _watch_same(ctx, account)
    return {"ok": True, "watches": data,
            "guidance": WATCH_NOTE + " For a pending approval, show its exact terms and use pepper_watch_respond only after the user decides."}


def _watch(ctx, args):
    account = _watch_account(ctx, "scout-intents:write")
    approval, quantity = args.get("approval"), args.get("quantity")
    if approval not in ("tell_me", "check_first", "just_buy") or type(quantity) is not int or not 1 <= quantity <= 8:
        raise Refusal("Choose 1 to 8 tickets and how the watch may act: tell_me, check_first or just_buy.")
    terms = {"xpEventId": _text(args.get("xpEventId"), "xpEventId"), "quantity": quantity,
             "capTotalUSD": _usd(args.get("capTotalUSD"), "The cap"),
             "deadlineAt": _text(args.get("deadlineAt"), "deadlineAt", 40), "approval": approval}
    if args.get("aimTotalUSD") is not None:
        terms["aimTotalUSD"] = _usd(args["aimTotalUSD"], "The aim")
    if args.get("note"):
        terms["note"] = _text(args["note"], "note", 200)
    # ponytail: the request key is a hash of the terms, so a lost reply replays the same draft instead of making a
    # second one. Identical terms cannot create another watch; add a saved key if users ever need that.
    key = "pepper-watch-" + sha256(json.dumps([account, terms], sort_keys=True).encode("utf-8")).hexdigest()[:32]
    data = _call(ctx, "create_scout_intent", {**terms, "idempotencyKey": key})
    _watch_same(ctx, account)
    return _watch_reply(data, "The watch is saved. If its status is draft it is not watching yet: show these exact terms and ask before pepper_watch_control activates it. If replayed is true, this is an earlier watch with the same terms.")


def _watch_control(ctx, args):
    account = _watch_account(ctx, "scout-intents:write")
    tool = WATCH_ACTIONS.get(args.get("action"))
    if not tool or args.get("confirmed") is not True:
        raise Refusal("Name activate, pause, resume or cancel, and obtain the user's explicit confirmation for this exact watch first.", "WATCH_CONFIRMATION_REQUIRED")
    data = _call(ctx, tool, {"scoutId": _scout_id(args.get("scoutId"))})
    _watch_same(ctx, account)
    return _watch_reply(data, "The returned status is the watch's actual state. Cancelling is permanent; a held payment follows the ParlayTix watch page.")


def _watch_respond(ctx, args):
    account = _watch_account(ctx, "scout-intents:write")
    response = args.get("response")
    if response not in ("approve", "decline") or args.get("confirmed") is not True:
        raise Refusal("Show the pending approval's exact terms and obtain the user's explicit approve or decline first.", "WATCH_CONFIRMATION_REQUIRED")
    data = _call(ctx, "respond_to_scout_intent_approval", {
        "scoutId": _scout_id(args.get("scoutId")), "decisionId": _text(args.get("decisionId"), "decisionId"), "response": response})
    _watch_same(ctx, account)
    return _watch_reply(data, "The answer is recorded. Approving is not a purchase; read pepper_watches for what happened next.")


def register(ctx):
    ctx.register_system_prompt_section("pepper.journey", JOURNEY_PROMPT, max_chars=2200)
    definitions = [
        ("pepper_start", "Start the guided ticket journey when the user says 'parlay this', conditionalize, or fractionalize. Ask about ticket ownership first; no IDs, sign-in or product writes needed.", _start, {}, []),
        ("pepper_connection", "Check ParlayTix connection and granted permissions.", _connection, {}, []),
        ("pepper_find", "Find real event and fan-ticket listing candidates using an explicit query.", _find,
         {"query": {"type": "string", "minLength": 3, "maxLength": 120},
          "page": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 1}}, ["query"]),
        ("pepper_select", "Read and save one ticket's exact terms for this profile and ParlayTix account. Does not reserve, buy or accept the condition.", _select,
         {"claimId": {"type": "string"}}, ["claimId"]),
        ("pepper_resume", "Restore this account's saved selection, refresh its terms and read actual checkout status. Never repeats review or purchase.", _resume, {}, []),
        ("pepper_forget", "Forget this account's local saved selection. Does not cancel a purchase or revoke consent.", _forget, {}, []),
        ("pepper_review", "Recheck the saved conditional fan ticket accepted by the user and return its wallet review link. Does not purchase or reserve.", _review,
         {"selectionId": {"type": "string", "description": "Copy the exact selectionId from pepper_select after showing its terms."},
          "conditionalTicketAccepted": {"type": "boolean", "description": "True only after the user accepts the conditional attendance and premium-loss outcome."}},
         ["selectionId", "conditionalTicketAccepted"]),
        ("pepper_status", "Read this account's current checkout and completed receipt for a card.", _status,
         {"claimId": {"type": "string"}}, ["claimId"]),
        ("pepper_owned", "Start Parlay this: list your whole tickets and existing fan rights with eligibility.", _owned,
         {"page": {"type": "integer", "minimum": 1, "maximum": 1000}}, []),
        ("pepper_questions", "Find deciding questions for your own verified whole ticket; copy exact source IDs.", _questions,
         {"collateralId": {"type": "string"}, "query": {"type": "string"}, "eventId": {"type": "string"}, "page": {"type": "integer"}}, ["collateralId"]),
        ("pepper_parlay", "Preview and save a Parlay split of your whole ticket into two complementary rights. Does not create rights.", _parlay,
         {"collateralId": {"type": "string"}, "eventId": {"type": "string"}, "conditionId": {"type": "string"}}, ["collateralId", "eventId"]),
        ("pepper_split", "Create the exact reviewed split only after the user's explicit confirmation and chosen keep outcome. Subsequent calls only read status.", _split,
         {"draftId": {"type": "string"}, "keepOutcomeId": {"type": "string"}, "confirmed": {"type": "boolean"}}, ["draftId", "keepOutcomeId", "confirmed"]),
        ("pepper_parlay_status", "Resume the saved Parlay draft or read its confirmed rights and actual listing. Recovers a lost creation response without resubmitting.", _parlay_status, {}, []),
        ("pepper_offer", "Save a proposed asking price and return wallet review for the other right. Preserves the chosen keep outcome; creates no listing.", _offer,
         {"draftId": {"type": "string"}, "askingPrice": {"type": "string", "description": "User-chosen price in USD/USDC units, at most two decimal places."}}, ["draftId", "askingPrice"]),
        ("pepper_parlay_forget", "Clear the exact local Parlay draft on request after preview or completed issuance. Does not cancel rights or listings; pending recovery records cannot be cleared.", _parlay_forget,
         {"draftId": {"type": "string"}, "confirmed": {"type": "boolean"}}, ["draftId", "confirmed"]),
        ("pepper_watches", "Read your Scout watches: stored terms, status, decisions and any pending approval. Pass scoutId for one watch. Starts, pays and buys nothing.", _watches,
         {"scoutId": {"type": "string"}, "cursor": {"type": "string"}}, []),
        ("pepper_watch", "Save a draft Scout watch for one event from pepper_find. It does not start watching, pay or buy.", _watch,
         {"xpEventId": {"type": "string", "description": "The exact providerEventId of the matched event from pepper_find."},
          "quantity": {"type": "integer", "minimum": 1, "maximum": 8},
          "capTotalUSD": {"type": "string", "description": "The most the user will pay in total for all tickets, for example 300.00."},
          "aimTotalUSD": {"type": "string", "description": "Optional total the user hopes for, at or below the cap."},
          "deadlineAt": {"type": "string", "description": "ISO time before the event starts when the watch should stop."},
          "approval": {"type": "string", "enum": ["tell_me", "check_first", "just_buy"],
                       "description": "tell_me only reports; check_first asks before acting; just_buy needs prepayment in ParlayTix."},
          "note": {"type": "string", "maxLength": 200}},
         ["xpEventId", "quantity", "capTotalUSD", "deadlineAt", "approval"]),
        ("pepper_watch_control", "Activate, pause, resume or permanently cancel one watch after the user's explicit confirmation. Never pays; a paid step returns a ParlayTix payment page.", _watch_control,
         {"scoutId": {"type": "string"}, "action": {"type": "string", "enum": ["activate", "pause", "resume", "cancel"]},
          "confirmed": {"type": "boolean"}}, ["scoutId", "action", "confirmed"]),
        ("pepper_watch_respond", "Approve or decline a watch's pending approval with its exact decisionId after the user decides. Approving is not a purchase.", _watch_respond,
         {"scoutId": {"type": "string"}, "decisionId": {"type": "string"}, "response": {"type": "string", "enum": ["approve", "decline"]},
          "confirmed": {"type": "boolean"}}, ["scoutId", "decisionId", "response", "confirmed"]),
    ]
    for name, description, operation, properties, required in definitions:
        def handler(args, _operation=operation, **kwargs):
            try:
                if not isinstance(args, dict):
                    raise Refusal("Tool arguments must be an object.")
                state = getattr(ctx, "state", None)
                if (not callable(getattr(ctx, "call_mcp", None))
                        or not all(callable(getattr(state, method, None)) for method in ("get", "set"))):
                    raise Refusal("Pepper requires Hermes's public call_mcp and persistent state APIs. Update Hermes before using Pepper.", "HERMES_API_REQUIRED")
                return json.dumps(_operation(ctx, args), ensure_ascii=False)
            except Refusal as exc:
                return json.dumps({"ok": False, "code": exc.code, "error": str(exc),
                                   "retryAutomatically": False})

        ctx.register_tool(name=name, toolset="pepper", description=description,
                          schema={"name": name, "description": description,
                                  "parameters": {"type": "object", "properties": properties,
                                                 "required": required, "additionalProperties": False}},
                          handler=handler)
