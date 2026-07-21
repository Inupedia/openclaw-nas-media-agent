# Deployment Instructions for Agents

When asked to install or deploy this repository, first read and follow `docs/AGENT_DEPLOY.md`.

Use `deploy/cli.py`; do not invent an alternative deployment procedure. Follow each JSON response's `status`, `nextAction`, and stable error code. Stop for user input when the deployer reports `manual_action_required`.

Never print or commit passwords, cookies, tokens, RPC secrets, or private endpoints. Do not perform real downloads, media-library writes, destructive permission changes, or other dangerous actions without explicit user confirmation.
