# Agent-First Quick Deployment README Design

**Date:** 2026-07-21  
**Branch:** `docs/agent-quick-deploy-readme`

## Goal

Reframe deployment documentation around two user-facing paths:

1. **Quick deployment (recommended):** the user copies one universal prompt into a terminal-capable coding Agent such as Codex, Claude Code, OpenCode/OpenCodex, or Cursor. The Agent clones or opens the repository, reads the repository deployment contract, invokes the deterministic deployer, asks only for information or actions that cannot be automated, and completes safe verification.
2. **Manual deployment:** the user directly runs the existing deterministic deployment commands and reviews each plan and action themselves.

The quick path must clearly state that the user only needs to copy the provided prompt and paste it into the Agent. It must not require tool-specific instructions.

## README structure

The deployment section will be reordered as follows:

### 1. Quick deployment with an Agent

Place this before manual deployment and label it as the recommended path.

It contains:

- one sentence stating the only required user action: copy the entire prompt and paste it into a terminal-capable Agent;
- examples of compatible Agent categories without maintaining separate instructions;
- a single fenced universal prompt;
- a short explanation of when the Agent may pause for user input;
- a link to `docs/AGENT_DEPLOY.md` for the authoritative execution contract.

The universal prompt must instruct the Agent to:

- deploy `https://github.com/Inupedia/openclaw-nas-media-agent` on the current NAS or Linux Docker host;
- first read `AGENTS.md`, `docs/AGENT_DEPLOY.md`, `docs/deployment/QUICKSTART.md`, `docs/deployment/SECURITY.md`, `docs/deployment/EXISTING_OPENCLAW.md`, `docs/deployment/QAS_LOGIN.md`, `docs/deployment/PROXY.md`, and `docs/deployment/TROUBLESHOOTING.md`;
- use `deploy/cli.py` and follow its JSON `status`, `nextAction`, and error codes;
- perform discovery and planning before changes;
- reuse existing OpenClaw, QAS, PanSou, aria2, networks, and mounts when safely identifiable;
- never guess paths, ports, credentials, or conflicting targets;
- ask the user only for missing facts, login/QR/captcha, conflict selection, or dangerous-operation confirmation;
- keep secrets out of chat, logs, reports, and commits;
- avoid real downloads and media-library writes without explicit confirmation;
- continue handling safe, automatable failures until `verify --level safe` completes;
- end with a deployment report covering status, containers, path mappings, manual actions, verification, and rollback.

The prompt must remain stable and delegate version-specific details to repository documentation and the deployer.

### 2. Manual deployment

Present the current deterministic command flow as the manual path:

```bash
git clone https://github.com/Inupedia/openclaw-nas-media-agent.git
cd openclaw-nas-media-agent
python3 deploy/cli.py init
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
python3 deploy/cli.py verify --level safe
```

Clarify that this path is for users who want to inspect and operate every step themselves. It is not the recommended path for most new users.

Legacy hand-built Compose, `.env`, Skill-copy, and routing instructions must not appear as a competing primary installation path. They may remain in advanced or troubleshooting documentation where still accurate.

## Documentation responsibilities

### `README.md`

- Product overview and capabilities.
- Recommended Agent quick-deployment entry.
- Manual deterministic-deployer entry.
- Links to detailed documentation.
- No tool-specific Agent guides.

### `AGENTS.md`

A short repository-level instruction file for tools that automatically read Agent guidance. It must state:

- deployment requests must first follow `docs/AGENT_DEPLOY.md`;
- the Agent must use `deploy/cli.py` instead of inventing a separate procedure;
- JSON `status` and `nextAction` drive continuation;
- secrets and destructive operations remain protected.

### `docs/deployment/QUICKSTART.md`

Rewrite as the user-facing quick-deployment page:

- state that the user only copies the universal prompt into an Agent;
- include the same canonical prompt as README to prevent contradictory wording;
- explain prerequisites: terminal access, Docker/Compose access, and an existing Compose-managed OpenClaw installation;
- explain expected user interruptions;
- link to the manual path for users who do not use an Agent.

### `docs/AGENT_DEPLOY.md`

Remain the authoritative execution contract for the Agent. Do not duplicate all of its detailed implementation instructions into README.

### Other deployment documents

Remain focused on their current specialist roles:

- `docs/deployment/EXISTING_OPENCLAW.md`: detailed manual/operator workflow;
- `docs/deployment/SECURITY.md`: credentials, permissions, protected paths, and confirmation rules;
- `docs/deployment/QAS_LOGIN.md`: login and initialization handling;
- `docs/deployment/PROXY.md`: PanSou and proxy handling;
- `docs/deployment/TROUBLESHOOTING.md`: failure diagnosis.

## Consistency rules

- README and QUICKSTART must contain the same universal prompt text.
- The quick path and manual path must both invoke the same deterministic deployer.
- The documentation must not promise automatic installation of OpenClaw from a blank host in this phase.
- Supported execution target remains an existing Compose-managed OpenClaw deployment on a standard Linux Docker/Compose host, including NAS platforms that expose that environment.
- Tool names are examples only; behavior requirements remain Agent-neutral.
- No real credentials, private endpoints, or destructive example commands may be included.

## Validation

Update documentation contract tests so they verify:

- the quick Agent deployment section appears before manual deployment;
- README explicitly tells the user to copy the complete prompt and paste it into an Agent;
- README and QUICKSTART contain the same canonical repository URL, required deployment documents, and `deploy/cli.py` requirement;
- `AGENTS.md` exists and points to `docs/AGENT_DEPLOY.md`;
- the manual command sequence remains documented;
- no private endpoints or credential values are introduced.

Run the full repository test suite and documentation command validation after implementation.

## Non-goals

- Separate prompt variants for Codex, Claude Code, Cursor, or other tools.
- Automatic OpenClaw installation on a blank host.
- Replacing `deploy/cli.py` with free-form Agent shell commands.
- Adding a web installer or NAS-vendor-specific UI workflow.
