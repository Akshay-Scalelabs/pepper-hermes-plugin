"""Pepper's first ticket-to-review flow, using Hermes-owned MCP and OAuth."""

from decimal import Decimal, InvalidOperation
import json
from urllib.parse import quote, urlsplit


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
    if not isinstance(permissions.get("scopes"), list):
        raise Refusal("ParlayTix did not return connection permissions.")
    return {"ok": True, "connection": permissions}


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
    claim_id = _text(args.get("claimId"), "claimId")
    listing_id = _text(args.get("listingId"), "listingId")
    version = _integer(args.get("expectedListingVersion"), "listing version")
    price = _price(args.get("expectedPrice"))
    currency = _text(args.get("currency"), "currency", 10)
    key = _text(args.get("idempotencyKey"), "idempotency key", 170)
    asset = _call(ctx, "get_ticket_asset", {"claimId": claim_id}).get("asset")
    if not isinstance(asset, dict) or asset.get("claimId") != claim_id:
        raise Refusal("The refreshed card does not match the selection.")
    listing = asset.get("listing")
    if (not isinstance(listing, dict) or listing.get("listingId") != listing_id
            or type(listing.get("version")) is not int or listing["version"] != version
            or listing.get("currency") != currency or _price(listing.get("price")) != price):
        raise Refusal("The listing terms changed. Refresh the offer and obtain fresh user acceptance.")
    ticket, nft = asset.get("ticket"), asset.get("nft")
    if (listing.get("status") != "live" or asset.get("tradingClosed") is not False
            or asset.get("isOwner") is not False or not isinstance(ticket, dict)
            or ticket.get("backingClass") != "live" or not isinstance(nft, dict)
            or nft.get("chainId") != 8453 or not isinstance(nft.get("actions"), list)
            or "buy" not in nft["actions"]):
        raise Refusal("This card is not currently available for a Base wallet purchase.")
    data = _call(ctx, "create_ticket_checkout", {
        "listingId": listing_id, "expectedListingVersion": version, "idempotencyKey": key,
    })
    if (data.get("listingId") != listing_id or type(data.get("listingVersion")) is not int
            or data["listingVersion"] != version or _price(data.get("askingPrice")) != price
            or _price(data.get("priceUSD")) != price):
        raise Refusal("The review terms changed. Refresh the offer and obtain fresh user acceptance.")
    return {**_checkout(data, claim_id), "asset": asset, "currency": currency}


def _status(ctx, args):
    claim_id = _text(args.get("claimId"), "claimId")
    return _checkout(_call(ctx, "get_ticket_checkout", {"claimId": claim_id}), claim_id)


def register(ctx):
    definitions = [
        ("pepper_connection", "Check ParlayTix connection and granted permissions.", _connection, {}, []),
        ("pepper_find", "Find real event and fan-ticket listing candidates using an explicit query.", _find,
         {"query": {"type": "string", "minLength": 3, "maxLength": 120},
          "page": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 1}}, ["query"]),
        ("pepper_review", "Recheck a user's accepted conditional fan ticket and return its ParlayTix wallet review link. Does not purchase or reserve.", _review,
         {"claimId": {"type": "string"}, "listingId": {"type": "string"},
          "expectedListingVersion": {"type": "integer", "minimum": 1},
          "expectedPrice": {"type": "string", "description": "Exact listed price accepted by the user, as a decimal string."},
          "currency": {"type": "string", "description": "Exact listing currency; do not infer from market probability."},
          "idempotencyKey": {"type": "string", "description": "Preserve this key and identical terms on retry."},
          "conditionalTicketAccepted": {"type": "boolean", "description": "True only after the user accepts the conditional attendance and premium-loss outcome."}},
         ["claimId", "listingId", "expectedListingVersion", "expectedPrice", "currency", "idempotencyKey", "conditionalTicketAccepted"]),
        ("pepper_status", "Read this account's current checkout and completed receipt for a card.", _status,
         {"claimId": {"type": "string"}}, ["claimId"]),
    ]
    for name, description, operation, properties, required in definitions:
        def handler(args, _operation=operation, **kwargs):
            try:
                if not isinstance(args, dict):
                    raise Refusal("Tool arguments must be an object.")
                return json.dumps(_operation(ctx, args), ensure_ascii=False)
            except Refusal as exc:
                return json.dumps({"ok": False, "code": exc.code, "error": str(exc),
                                   "retryAutomatically": False})

        ctx.register_tool(name=name, toolset="pepper", description=description,
                          schema={"name": name, "description": description,
                                  "parameters": {"type": "object", "properties": properties,
                                                 "required": required, "additionalProperties": False}},
                          handler=handler)
