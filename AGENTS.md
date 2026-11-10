# Pepper for Hermes

This repository owns the native Pepper plugin, its Hermes procedure and its tests. `plugin.yaml` declares the tools. `__init__.py` registers the handlers and the guided entry point. The [README](README.md) covers installation, OAuth and reviewer scenarios.

Use `ctx.call_mcp` for product operations and `ctx.state` for account-bound drafts. The public ParlayTix MCP and Marketplace own eligibility and issuance. Terminal owns the user's wallet confirmation.

Run `python3 -m unittest discover -s tests -v` from this checkout. Use a stock Hermes Python environment to exercise native prompt registration and storage. Run `hermes plugins validate .` for the package contract.
