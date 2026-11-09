# Pepper for Hermes

Pepper adds “Parlay this” to your existing Hermes assistant on Telegram or Discord: choose your verified whole ticket, preview two complementary rights, confirm the split, keep one outcome and review an offer for the other in your wallet. Its saved draft survives a chat interruption, binds the exact accepted terms and recovers uncertain creation by reading the original request. It also supports finding and reviewing existing fan-ticket offers. No separate bot or Hermes fork is needed.

## Status

Version 0.3.0 adds the holder composer and requires the new public holder adapter. It is a build candidate until the Marketplace and Public MCP changes are released and a real Telegram test passes. Holder creation is separately gated by `TICKET_HOLDER_SPLIT_ENABLED` on Marketplace, off by default. The first supported asset is a verified, unsplit whole ticket. Current Base fan tickets cannot be re-split. Both new rights initially mint to the account's existing wallet; listing remains a separate Terminal wallet action. No live holder split, listing or Telegram completion is claimed by local tests.

Stock Hermes v0.23.0 completed OAuth, discovered ParlayTix's 34-tool catalog and exercised a real search → selection → refreshed review-link flow on November 8, 2026. The selected Lakers-at-Spurs fan ticket was 20.17 USDC, conditional on a Warriors win in a separate game. The canonical ParlayTix page rendered matching event, seat, condition and numeric price. `purchaseCompleted` was false; no funds were spent. Prices and availability must be refreshed for each run.

All 21 tests pass on official stock Hermes 0.23.0 and the personal runtime reporting 0.21.5. Native package validation passes all 15 checks on stock and all 13 checks on the personal runtime, with no warnings. Version 0.2.1 adds a host-API compatibility check and its test. Product responses in automated tests are simulated. Successful live fresh-chat restoration is still unverified: its native approval timed out, and Pepper stopped without retrying. Desktop acceptance remains pending. No private Hermes fork is required.

## Setup

The package accepts Hermes 0.21.5+ and checks for the public `call_mcp` and persistent-state APIs before a tool performs product work. The contract and storage tests passed on the personal runtime reporting 0.21.5 as well as official stock 0.23.0. The 0.21.5 check covers that installed build; no matching official release tag was available for a separate source comparison. Official 0.23.0 remains the release used for live product acceptance.

For Telegram or Discord, install Pepper and configure its MCP connection in the profile that already serves those channels. Add `pepper` and `parlaytix-prod` to that channel's existing toolsets when it uses an explicit toolset list. Preserve its current tools, bot identity and access controls. A separate development profile does not make Pepper available to the existing bot. Hermes owns channel delivery and native approval prompts; the plugin neither creates a chatbot nor starts a messaging adapter.

Use a dedicated profile for development. For normal use, choose the existing profile that serves your Hermes conversations. Install/enable this local package using the installed host's `hermes plugins` commands, and run the following connection commands in that same profile. Preserve its other plugins and settings. No separate bot or Hermes core patch is needed.

ParlayTix must admit the exact native Hermes OAuth client in both of its client allowlists before product access works. The native CLI uses CIMD and PKCE; ParlayTix does not support Dynamic Client Registration. The client ID is `https://nousresearch.github.io/hermes-agent/docs/oauth/client-metadata.json`; the integration endpoint is `https://mcp.parlaytix.xyz/mcp`.

For a fresh Pepper profile with no existing `parlaytix-prod` entry, stage the server disabled and configure trust before connecting:

```sh
hermes config set mcp_servers.parlaytix-prod.enabled false
hermes config set mcp_servers.parlaytix-prod.url https://mcp.parlaytix.xyz/mcp
hermes config set mcp_servers.parlaytix-prod.auth oauth
hermes config set mcp_servers.parlaytix-prod.trust untrusted
hermes config set mcp_servers.parlaytix-prod.headers.User-Agent HermesAgent
hermes config set mcp_servers.parlaytix-prod.oauth.user_agent HermesAgent
hermes config set plugins.entries.pepper.mcp_allowlist '["parlaytix-prod"]'
hermes mcp login parlaytix-prod
hermes config set mcp_servers.parlaytix-prod.enabled true
hermes mcp test parlaytix-prod
```

The equivalent non-secret configuration is:

```yaml
mcp_servers:
  parlaytix-prod:
    url: https://mcp.parlaytix.xyz/mcp
    auth: oauth
    trust: untrusted
    headers:
      User-Agent: HermesAgent
    oauth:
      user_agent: HermesAgent
plugins:
  entries:
    pepper:
      mcp_allowlist: [parlaytix-prod]
```

Use Hermes's configuration commands to merge these settings; never replace the whole profile config. Grant only this server. MCP calls without an explicit read-only hint may ask for approval under `untrusted`; preserve those prompts. Unattended calls must not disable approval or use YOLO to get past them.

For a remote VPS, run normal CLI OAuth there, and forward its selected callback port to the local machine before completing browser consent. Native CIMD selects ports 27890–27894. When Hermes runs directly on the VPS host, the tunnel is:

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:SELECTED_PORT:127.0.0.1:SELECTED_PORT PERSONAL_VPS
```

Replace the placeholders with the observed port and confirmed personal host. Do not set `oauth.redirect_port`, `redirect_uri` or another client's identity to force the callback; these change native CIMD selection. Complete consent through the normal user-owned sign-in. Never paste the callback URL/code or copy token files. Desktop-initiated sign-in and reuse of CLI credentials are separate acceptance checks.

If Hermes runs inside a container, its callback listener binds **the container's loopback interface**. SSH forwarding to the VPS host's loopback, or to the container IP alone, does not reach that listener. Establish an isolated forward into the container network namespace and its loopback listener, then tunnel that host-side forward over SSH. Verify this path before consent; do not expose the callback port publicly or change the unrelated project's container networking.

If the connection has an explicit tool include-list, preserve its entries and add `list_my_ticket_inventory`, `list_my_ticket_assets`, `search_ticket_split_questions`, `preview_ticket_split`, `create_ticket_split` and `get_ticket_split`. These are fixed product tools; do not grant an arbitrary passthrough.

Start a new Hermes chat after setup. Ask: “Use Pepper. Show tickets I own that I can parlay.” Choose an eligible ticket and deciding question; review both outcomes before accepting the split. When returning, ask “Resume my Pepper parlay.” For the existing buyer flow, ask “Use Pepper to find fan tickets for [event]” and accept the exact selected conditional offer before requesting its wallet review.

If the normal connection test reports `OAUTH_CLIENT_NOT_ALLOWED`, sign-in may already have completed: the protected resource is refusing the client. The platform owner must admit the exact Hermes identity in both `PUBLIC_MCP_OAUTH_CANARY_ALLOWLIST_JSON` and `MCP_OAUTH_CANARY_ALLOWLIST_JSON`, preserving existing entries and fields. Do not change client identity, enable DCR or repeat login to fix a server allowlist. Let Hermes refresh its existing credentials and retest after admission.

## Tools

| Tool | Behavior |
| --- | --- |
| `pepper_connection` | Read the current account and granted permissions |
| `pepper_find` | Explicit-query event search and listing candidates, preserving coverage |
| `pepper_select` | Fetch a ticket and save its terms for the verified account/profile |
| `pepper_resume` | Restore the selection, compare fresh terms and read actual checkout status |
| `pepper_forget` | Clear only this account/profile's saved selection |
| `pepper_review` | Recheck the saved terms and prepare the canonical wallet review link |
| `pepper_status` | Read checkout status and matching finalized purchase receipt |
| `pepper_owned` | Read whole-ticket eligibility and existing held rights |
| `pepper_questions` | Discover deciding questions for the owned ticket |
| `pepper_parlay` | Save the exact two-outcome preview without issuing rights |
| `pepper_split` | Refresh accepted terms, save the request key, then submit the explicitly confirmed split |
| `pepper_parlay_status` | Recover/resume the same request and check minted rights and actual listing |
| `pepper_offer` | Save the other right's proposed ask and return its wallet review page |
| `pepper_parlay_forget` | Clear a preview or completed local draft without cancelling product state |

The plugin uses documented `ctx.call_mcp`, so Hermes owns OAuth, credentials, refresh, transport and approvals. It makes requests only to the configured `parlaytix-prod` server and does not expose an arbitrary tool passthrough. It reads no credential files, runs no shell commands, starts no background work, and sends no telemetry. Responses are treated as untrusted data. Confirmed holder splitting creates real rights through the existing issuer. Preview, status and listing-review preparation do not sign or spend from the customer's wallet.

The account-bound Parlay record stores one current draft: exact ticket/source identities, outcome rules, preview hashes, chosen keep outcome, stable request key, split ID and proposed offer. It contains no credentials or wallet signing payload. A lost write response keeps that request key for read-only recovery. An explicit listing review opens the existing card page: the user enters the price there and reviews fees before signing. Pepper verifies the resulting listing against the saved ask; it does not equate a listing with a sale.

`ctx.state` stores one selected ticket per ParlayTix account under the active Hermes profile's plugin-data directory. Stored data includes the account ID, generated selection ID, selected/read timestamps and a bounded projection of the ticket, condition, listing, deadlines and market observation. It excludes credentials, raw wallet payloads and checkout receipts. Hermes writes this JSON atomically with mode `0600`. These local records are not encrypted by Pepper; the profile's owner can read them. Pepper checks live account identity and ticket permissions before restoring a record and rechecks identity after the product reads. Revoked access cannot fall back to an offline saved card. Local selection state is not authoritative proof of price, inventory, a running watch or ownership.

`pepper_forget` clears the current account's record after a successful identity check. Other accounts/profiles keep their own selections. Uninstalling a package does not necessarily delete Hermes's plugin-data directory; manage retained plugin data separately if the account can no longer sign in. Resuming never requests a new checkout, changes the saved accepted terms or automatically retries an unknown response.

Explicit stock-Hermes approval denial and reauthentication responses retain their stop instructions through bounded error categories. Unknown responses never authorize automatic retries: inspect product status and connection state first. Raw upstream errors are not returned.

## Verification

```sh
python3 -m unittest discover -s tests -v
hermes plugins doctor /absolute/path/to/pepper-hermes-plugin --ci
hermes plugins validate /absolute/path/to/pepper-hermes-plugin --install-deps
```

The tests exercise registered handlers through the public host seam: envelope variations, refusal/truncation, account/profile separation, restart and unknown-response recovery, changed ticket/condition/listing terms, canonical handoff validation and receipt-based status. They do not prove live inventory, transport approvals, deployment or a funded purchase. Validate on the actual declared stock release and perform an interactive live run before submission.

Disable/remove the package with Hermes's native plugin manager. Remove the dedicated MCP connection with `hermes mcp remove parlaytix-prod` only when it belongs exclusively to this test profile. Local removal and provider consent revocation are separate actions.
