# GitHub Publication Design

## Goal

Publish the existing NAS media resource agent as the public GitHub repository
`openclaw-nas-media-agent`. The repository is for agent-driven installation on a
NAS. UGREEN NAS is the first verified platform, while other Docker-capable NAS
systems may adapt the documented paths and mounts.

## Repository entry point

`README.md` is the first-use interface. Its opening section must immediately
tell readers to give the repository to their NAS agent and include a copyable
Chinese installation prompt. The prompt instructs the agent to clone the
repository, inspect `SKILL.md` and the README, audit paths and permissions,
configure required services, run tests, and ask before making destructive or
security-sensitive changes.

A conventional `git clone` flow follows the agent-first prompt for users who
prefer manual installation.

## README structure

The README will cover, in this order:

1. Copyable “give this project to your agent” installation prompt.
2. Git clone and Skill placement commands.
3. What the project does and does not do.
4. UGREEN NAS Docker mounts and recommended directory layout.
5. QAS and aria2 prerequisites plus environment variables, without real
   credentials.
6. The operational flow: NAS-first lookup, comprehensive candidate inspection,
   user specification choice, staging download, validation, and confirmed
   organization.
7. Example natural-language requests and corresponding `mediactl` commands.
8. Safety boundaries, troubleshooting, testing, and compatibility notes.

## Safety contract

- Downloads enter `/volume2/downloads` first.
- `/volume2/影视` and `/volume3/临时影视` are permanent protected libraries:
  OpenClaw cannot delete, overwrite, clean, or move existing content out.
- Search and preview never authorize download.
- Downloads are incremental; ambiguous episode selection stops for user input.
- The agent executes only the fixed `mediactl` entrypoint through an allowlist.
- Documentation contains placeholders only and never publishes NAS addresses,
  passwords, cookies, tokens, RPC secrets, or user-specific identifiers.

## Publication

Merge `feature/safe-media-agent` into `feature/core-agent`, verify the complete
test suite on the merged result, add and verify the README, then publish the
public repository `openclaw-nas-media-agent`. The published default branch will
contain the full history and be checked through the GitHub repository page
after push.

