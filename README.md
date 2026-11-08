# Pepper for Hermes

Pepper helps find a ParlayTix fan ticket, explains its condition, rechecks accepted terms and returns the customer's wallet-review link. This is the first integration slice: four native tools and a bundled procedure. Desktop UI and durable saved selections come later.

## Status

Local contract tests use simulated host responses. A real stock-Hermes OAuth and listing-to-review run is still required before claiming live acceptance. The intended minimum host is official Hermes v0.23.0; no fork or private backend installation is needed.

## Isolated setup

Use a dedicated Pepper profile on the personal Hermes host, preserving other projects, services, connections and credentials. Install/enable this local package using the installed host's `hermes plugins` commands. Run all following commands in that same dedicated profile. Do not upgrade or restart a shared host merely to install Pepper.

ParlayTix must admit the exact native Hermes OAuth client in both of its client allowlists before sign-in. The native CLI uses CIMD and PKCE; ParlayTix does not support Dynamic Client Registration. The integration endpoint is `https://mcp.parlaytix.xyz/mcp`.

For a fresh Pepper profile with no existing `parlaytix-prod` entry, stage the server disabled and configure trust before connecting:

```sh
hermes config set mcp_servers.parlaytix-prod.enabled false
hermes config set mcp_servers.parlaytix-prod.url https://mcp.parlaytix.xyz/mcp
hermes config set mcp_servers.parlaytix-prod.auth oauth
hermes config set mcp_servers.parlaytix-prod.trust untrusted
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

Start a new Hermes session after setup. Ask: “Pepper, find fan tickets for [event].” Then select an actual offer and explicitly accept its condition before asking Pepper to prepare review. `pepper_review` refreshes the card and verifies its listing/version/price/currency, Base purchase eligibility and canonical review URL. An actual customer confirmation remains in Terminal.

## Tools

| Tool | Behavior |
| --- | --- |
| `pepper_connection` | Read the current account and granted permissions |
| `pepper_find` | Explicit-query event search and listing candidates, preserving coverage |
| `pepper_review` | Recheck accepted terms and prepare the canonical wallet review link |
| `pepper_status` | Read checkout status and matching finalized purchase receipt |

The plugin uses documented `ctx.call_mcp`, so Hermes owns OAuth, credentials, refresh, transport and approvals. It makes requests only to the configured `parlaytix-prod` server and does not expose an arbitrary tool passthrough. It reads no credential files, runs no shell commands, stores no customer state, starts no background work, and sends no telemetry. Responses are treated as untrusted data. Review preparation writes nothing and spends no funds on the current public MCP contract.

Explicit stock-Hermes approval denial and reauthentication responses retain their stop instructions through bounded error categories. Unknown responses never authorize automatic retries: inspect product status and connection state first. Raw upstream errors are not returned.

## Verification

```sh
python3 -m unittest discover -s tests -v
hermes plugins doctor /absolute/path/to/pepper-hermes-plugin --ci
hermes plugins validate /absolute/path/to/pepper-hermes-plugin --install-deps
```

The tests exercise registered handlers through the public host seam: envelope variations, refusal/truncation, accepted-term comparison, canonical handoff validation and receipt-based status. They do not prove OAuth, live inventory, transport approvals, deployment or a funded purchase. Validate on the actual declared stock release and perform an interactive live run before submission.

Disable/remove the package with Hermes's native plugin manager. Remove the dedicated MCP connection with `hermes mcp remove parlaytix-prod` only when it belongs exclusively to this test profile. Local removal and provider consent revocation are separate actions.
