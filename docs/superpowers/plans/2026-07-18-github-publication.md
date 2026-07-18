# GitHub Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the completed NAS media agent, document agent-first installation, and publish it as the public GitHub repository `openclaw-nas-media-agent`.

**Architecture:** Preserve the existing Python Skill and fixed `mediactl` entrypoint. Add a README contract test so the public installation and safety promises remain visible, then publish the verified merged branch as the repository default branch.

**Tech Stack:** Git, GitHub Desktop/system Git credentials, GitHub web UI or GitHub CLI, Python `unittest`, Markdown.

## Global Constraints

- The repository is public and named `openclaw-nas-media-agent`.
- The README starts with a copyable prompt that tells a NAS agent how to install the project.
- UGREEN NAS is the verified platform; other Docker-capable NAS systems are compatibility targets, not verified claims.
- Downloads enter `/volume2/downloads` before organization.
- `/volume2/影视` and `/volume3/临时影视` are permanent protected libraries.
- No NAS address, username, password, token, cookie, RPC secret, device ID, or user-specific gateway credential may be published.
- The complete Python test suite must pass before push.

---

### Task 1: Merge the completed Skill

**Files:**
- Merge: `feature/safe-media-agent` into `feature/core-agent`

**Interfaces:**
- Consumes: verified commits on `feature/safe-media-agent`
- Produces: one merged working tree containing the complete Skill

- [ ] **Step 1: Confirm both worktrees are clean**

Run:

```powershell
git -C . status --short
git -C .worktrees/safe-media-agent status --short
```

Expected: both commands print no modified or untracked files.

- [ ] **Step 2: Merge without rewriting history**

Run:

```powershell
git merge --no-ff feature/safe-media-agent -m "merge: complete safe NAS media agent"
```

Expected: merge succeeds on `feature/core-agent`.

- [ ] **Step 3: Run the merged test suite**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
```

Expected: 103 tests pass and three Windows symlink tests are skipped.

- [ ] **Step 4: Remove the merged worktree and branch**

Run from the repository root:

```powershell
git worktree remove .worktrees/safe-media-agent
git worktree prune
git branch -d feature/safe-media-agent
```

Expected: the worktree is removed and the fully merged feature branch is deleted.

### Task 2: Build the agent-first README

**Files:**
- Create: `README.md`
- Create: `tests/test_readme_contract.py`

**Interfaces:**
- Consumes: commands and safety rules in `SKILL.md`
- Produces: public installation documentation with testable required sections

- [ ] **Step 1: Determine the authenticated GitHub owner**

Prefer the signed-in GitHub web session. If GitHub CLI is authenticated, run:

```powershell
$owner = gh api user --jq .login
```

Expected: `$owner` contains one GitHub login name without exposing any token.

- [ ] **Step 2: Create the empty public repository**

Create `openclaw-nas-media-agent` under `$owner` without initializing README,
`.gitignore`, or license files.

Expected: an empty public repository exists at
`https://github.com/$owner/openclaw-nas-media-agent`.

- [ ] **Step 3: Add a failing README contract test**

Create `tests/test_readme_contract.py` with assertions that `README.md` contains:

```python
required = (
    "请把这个 GitHub 项目安装到我的 NAS",
    "git clone",
    "UGREEN",
    "/volume2/downloads",
    "/volume2/影视",
    "/volume3/临时影视",
    "mediactl",
    "QAS_BASE_URL",
    "ARIA2_RPC_URL",
)
```

The test must also reject the known private host prefix, credential labels with values, and user-specific gateway credentials.

- [ ] **Step 4: Verify the contract test fails**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_readme_contract -v
```

Expected: failure because `README.md` does not exist.

- [ ] **Step 5: Write `README.md`**

The document must contain, in order:

1. Project name and one-sentence NAS/OpenClaw positioning.
2. A copyable Chinese prompt beginning `请把这个 GitHub 项目安装到我的 NAS`.
3. Manual `git clone https://github.com/$owner/openclaw-nas-media-agent.git`
   instructions using the authenticated owner discovered in Step 1.
4. Capability summary and non-goals.
5. UGREEN Docker volume mapping example.
6. Required QAS/aria2 environment variable names with placeholder values only.
7. NAS-first search, specification choice, staging, validation, and organization flow.
8. Natural-language and `mediactl` examples.
9. Permanent protected-library rules and troubleshooting.
10. Test command and compatibility statement.

- [ ] **Step 6: Verify the README contract**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_readme_contract -v
```

Expected: all README contract tests pass.

- [ ] **Step 7: Commit the README**

Run:

```powershell
git add README.md tests/test_readme_contract.py
git commit -m "docs: add agent-first NAS installation guide"
```

### Task 3: Verify the public source tree

**Files:**
- Inspect: all tracked files

**Interfaces:**
- Consumes: merged repository and README
- Produces: evidence that the public tree is tested and contains no known private values

- [ ] **Step 1: Run the complete test suite**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
```

Expected: all tests pass, with only the three known Windows symlink skips.

- [ ] **Step 2: Scan tracked content for private values**

Run:

```powershell
git grep -n -I -E "192\.168\.31\.242|Inupedia@|Forgiveme@|OPENCLAW_GATEWAY_TOKEN: \"[^\"]+\"|DEVICE_ID: [a-f0-9]{32}"
```

Expected: no matches.

- [ ] **Step 3: Check formatting and scope**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and a clean working tree.

### Task 4: Create and publish the public GitHub repository

**Files:**
- Modify: local Git remote configuration
- Update if needed: `README.md` clone URL

**Interfaces:**
- Consumes: GitHub Desktop/system authentication or an authenticated GitHub web session
- Produces: public repository URL and pushed default branch

- [ ] **Step 1: Rename the local publication branch and push**

Run:

```powershell
git branch -M main
git remote add origin "https://github.com/$owner/openclaw-nas-media-agent.git"
git push -u origin main
```

Expected: GitHub Desktop/system credentials authorize the push and `main`
tracks `origin/main`.

- [ ] **Step 2: Verify the published repository**

Open the repository page and confirm:

- visibility is public;
- README renders with the agent-first prompt at the top;
- the default branch is `main`;
- source files and tests are present;
- the clone command uses the final repository URL.
