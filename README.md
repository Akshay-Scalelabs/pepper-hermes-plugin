# Pepper for Hermes

Pepper helps find a ParlayTix fan ticket, saves its exact terms, rechecks accepted terms and returns the customer's wallet-review link. The saved selection survives a new chat or restart, with fresh account and status checks on return. Desktop UI remains separate work.

## Status

Stock Hermes v0.23.0 completed OAuth, discovered ParlayTix's 34-tool catalog and exercised a real search → selection → refreshed review-link flow on November 8, 2026. The selected Lakers-at-Spurs fan ticket was 20.17 USDC, conditional on a Warriors win in a separate game. The canonical ParlayTix page rendered matching event, seat, condition and numeric price. `purchaseCompleted` was false; no funds were spent. Prices and availability must be refreshed for each run.

All 20 tests pass in stock Hermes (19 handler contracts plus one real host-storage test), and all 15 package validation checks pass. Product responses in automated tests are simulated. Successful live fresh-chat restoration is still unverified: its native approval timed out, and Pepper stopped without retrying. Desktop acceptance remains pending. No private Hermes fork is required.

## Isolated setup

Use a dedicated Pepper profile on the personal Hermes host, preserving other projects, services, connections and credentials. Install/enable this local package using the installed host's `hermes plugins` commands. Run all following commands in that same dedicated profile. Do not upgrade or restart a shared host merely to install Pepper.

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

Start a new Hermes session after setup. Ask: “Pepper, find fan tickets for [event].” Select an actual offer, inspect its saved terms and explicitly accept its condition before asking Pepper to prepare review. `pepper_review` takes the saved selection ID, refreshes the card and verifies its event/seat/condition/deadlines and listing/version/price/currency, Base purchase eligibility and canonical review URL. An actual customer confirmation remains in Terminal. In a new chat, ask “Pepper, resume my saved ticket.”

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

The plugin uses documented `ctx.call_mcp`, so Hermes owns OAuth, credentials, refresh, transport and approvals. It makes requests only to the configured `parlaytix-prod` server and does not expose an arbitrary tool passthrough. It reads no credential files, runs no shell commands, starts no background work, and sends no telemetry. Responses are treated as untrusted data. Review preparation writes nothing to ParlayTix and spends no funds on the current public MCP contract.

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
