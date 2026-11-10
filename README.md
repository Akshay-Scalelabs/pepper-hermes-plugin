# Pepper for Hermes

Say **“parlay this”** to your existing Hermes assistant on Discord or Telegram. Pepper asks whether you already have a ticket or want to find one, then guides ticket selection, the deciding outcome you want to keep, split confirmation and a price for listing the other side. Its saved draft survives a chat interruption, binds exact accepted terms and recovers uncertain creation by reading the original request. No separate bot or Hermes fork is needed.

## Install the review build

Accept the private GitHub invitation. Run these commands on the host that serves your Discord or Telegram chat, using the same Hermes profile as the gateway. The first command checks that Git can read the repository with your own GitHub credentials. The second installs the Pepper 0.4.0 revision tested on Discord. Stock Hermes 0.23.0 is the reference host.

```sh
git ls-remote https://github.com/Akshay-Scalelabs/pepper-hermes-plugin.git HEAD
hermes plugins install Akshay-Scalelabs/pepper-hermes-plugin --ref 24f0fc2a7a1c82aa27675b99cf3e854bc43a1ced --enable
```

Complete the ParlayTix connection below with your own account. If your channel has an explicit toolset list, include `pepper` and `parlaytix-prod` alongside its existing tools. Reload the gateway that serves the profile after configuration and start a new conversation. Say **“parlay this”**.

An account with a verified, unsplit whole ticket can preview both rights. Live issuance also requires Marketplace to admit the exact account and ticket. The existing development grant covers one pair. A reviewer using a different account can test discovery, ticket review, empty inventory and eligible previews. Live issuance for that reviewer requires a separate grant. Both issued rights go to the holder’s wallet. The holder confirms a listing in Terminal.

## Setup

Pepper uses the native `register_system_prompt_section`, `call_mcp` and persistent-state APIs. Hermes owns the chat, OAuth and tool approvals.

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

Start a new Hermes chat after setup or this upgrade so it receives Pepper’s native prompt section. Say **“parlay this”**. Answer the ownership question, choose an eligible ticket and deciding question, then review both outcomes before accepting the split. You do not need to name Pepper or supply internal IDs. When returning, ask “Resume my Pepper parlay.” For the existing buyer flow, ask “Use Pepper to find fan tickets for [event]” and accept the exact selected conditional offer before requesting its wallet review.

If the normal connection test reports `OAUTH_CLIENT_NOT_ALLOWED`, sign-in may already have completed: the protected resource is refusing the client. The platform owner must admit the exact Hermes identity in both `PUBLIC_MCP_OAUTH_CANARY_ALLOWLIST_JSON` and `MCP_OAUTH_CANARY_ALLOWLIST_JSON`, preserving existing entries and fields. Do not change client identity, enable DCR or repeat login to fix a server allowlist. Let Hermes refresh its existing credentials and retest after admission.

## Tools

| Tool | Behavior |
| --- | --- |
| `pepper_start` | Start the guided ownership/find-ticket conversation without product calls or state changes |
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

Try these cases from your own Hermes chat.

| Try | Expected result |
| --- | --- |
| Say “parlay this” in a new chat. | Pepper asks whether you have a ticket or want to find one. The opening makes no product call. |
| Choose “I have a ticket” with an empty or ineligible inventory. | Pepper explains what your account holds. It cannot create a split from an event name. |
| Preview an eligible ticket, then open a new chat and say “resume my Pepper parlay.” | The same account sees its draft and refreshed terms. Another account cannot resume it. |
| Decline a native tool approval. | Pepper stops. A new attempt requires a new direction from you. |
| Choose “find one,” select an offer and open wallet review. | The Terminal page shows the selected terms. Opening review does not complete a purchase. |
| With a separately admitted ticket, keep one outcome and review the other at your chosen price. | Pepper asks for exact split confirmation. Terminal asks for the listing signature. Pepper reports a listing only after it reads an active listing for the correct right and ask. |

The automated suite also exercises changed terms and recovery after a lost issuer response. Run it from a checkout with the stock Hermes Python environment to include the two native host tests.

```sh
python3 -m unittest discover -s tests -v
hermes plugins doctor /absolute/path/to/pepper-hermes-plugin --ci
hermes plugins validate /absolute/path/to/pepper-hermes-plugin
```

The tests exercise registered handlers through the public host seam: envelope variations, refusal/truncation, account/profile separation, restart and unknown-response recovery, changed ticket/condition/listing terms, canonical handoff validation and receipt-based status. They do not prove live inventory, transport approvals, deployment or a funded purchase. Validate on the actual declared stock release and perform an interactive live run before submission.

Disable/remove the package with Hermes's native plugin manager. Remove the dedicated MCP connection with `hermes mcp remove parlaytix-prod` only when it belongs exclusively to this test profile. Local removal and provider consent revocation are separate actions.
