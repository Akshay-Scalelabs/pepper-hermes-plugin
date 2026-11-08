"""Pepper's ticket-to-review flow, using Hermes-owned MCP, OAuth and state."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from urllib.parse import quote, urlsplit
from uuid import uuid4


SERVER = "parlaytix-prod"


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
    nft_source = asset.get("nft") if isinstance(asset.get("nft"), dict) else {}
    nft["chainId"] = nft_source.get("chainId") if type(nft_source.get("chainId")) is int else None
    listed = asset.get("listing")
    listing = None
    if isinstance(listed, dict):
        listing = {**_fields(listed, ("status", "expiresAt")),
                   "listingId": _text(listed.get("listingId"), "listingId"),
                   "version": _integer(listed.get("version"), "listing version"),
                   "price": str(_price(listed.get("price"))),
                   "currency": _text(listed.get("currency"), "currency", 10)}
    pricing = asset.get("pricing") if isinstance(asset.get("pricing"), dict) else {}
    probability = pricing.get("probability")
    if (type(probability) not in (int, float) or not 0 <= probability <= 1
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


def register(ctx):
    definitions = [
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
