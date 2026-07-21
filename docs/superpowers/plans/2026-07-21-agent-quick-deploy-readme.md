# Agent Quick Deployment README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent-assisted deployment the recommended path by giving users one universal prompt to paste into a terminal-capable coding Agent, while preserving the deterministic deployer as the manual path.

**Architecture:** Documentation remains split by audience. `README.md` and `docs/deployment/QUICKSTART.md` expose the same canonical universal prompt; `AGENTS.md` provides repository-level Agent guidance; `docs/AGENT_DEPLOY.md` remains the authoritative execution contract. Tests compare the quick-deployment contract across files and protect the manual command flow.

**Tech Stack:** Markdown, Python `unittest`, existing GitHub Actions documentation validation.

## Global Constraints

- The quick path must tell the user that their only required action is to copy the entire prompt and paste it into a terminal-capable Agent.
- The universal prompt must be tool-neutral and may list Codex, Claude Code, Cursor, OpenCode, and OpenCodex only as examples.
- README and `docs/deployment/QUICKSTART.md` must contain the same canonical prompt text.
- Both quick and manual paths must use `deploy/cli.py`; free-form replacement deployment flows are forbidden.
- The documentation must not promise automatic installation of OpenClaw on a blank host.
- The supported target remains an existing Compose-managed OpenClaw deployment on Linux Docker/Compose, including NAS platforms exposing that environment.
- No real credentials, private endpoints, or destructive example commands may be introduced.

---

### Task 1: Add documentation contract tests

**Files:**
- Modify: `tests/test_readme_contract.py`
- Create: `tests/test_agent_quickstart_contract.py`

**Interfaces:**
- Consumes: Markdown files at `README.md`, `AGENTS.md`, `docs/AGENT_DEPLOY.md`, and `docs/deployment/QUICKSTART.md`.
- Produces: Tests that define section ordering, canonical prompt boundaries, required document references, manual command preservation, and secret-safety constraints.

- [ ] **Step 1: Write the failing README ordering test**

Update `test_readme_starts_with_agent_first_installation` so it asserts:

```python
quick_install = content.index("## 快速部署：复制给 Agent")
manual_install = content.index("## 手动部署")
self.assertLess(quick_install, manual_install)
self.assertIn("只需要复制下面整段内容", content[quick_install:manual_install])
```

- [ ] **Step 2: Write the failing cross-file prompt contract test**

Create `tests/test_agent_quickstart_contract.py` with a helper that extracts text between these exact markers:

```markdown
<!-- AGENT_QUICK_DEPLOY_PROMPT_START -->
<!-- AGENT_QUICK_DEPLOY_PROMPT_END -->
```

Assert that README and QUICKSTART prompt bodies are identical and contain:

```text
https://github.com/Inupedia/openclaw-nas-media-agent
AGENTS.md
docs/AGENT_DEPLOY.md
docs/deployment/QUICKSTART.md
docs/deployment/SECURITY.md
docs/deployment/EXISTING_OPENCLAW.md
docs/deployment/QAS_LOGIN.md
docs/deployment/PROXY.md
docs/deployment/TROUBLESHOOTING.md
deploy/cli.py
verify --level safe
```

Also assert the prompt does not claim blank-host OpenClaw installation.

- [ ] **Step 3: Write the failing AGENTS.md contract test**

Assert `AGENTS.md` exists and contains:

```text
docs/AGENT_DEPLOY.md
deploy/cli.py
status
nextAction
```

Assert it prohibits alternative free-form deployment and secret disclosure.

- [ ] **Step 4: Run focused tests and confirm red state**

Run:

```bash
python3 -m unittest tests.test_readme_contract tests.test_agent_quickstart_contract -v
```

Expected: failures because the new section markers and `AGENTS.md` do not yet exist.

- [ ] **Step 5: Commit the red tests**

```bash
git add tests/test_readme_contract.py tests/test_agent_quickstart_contract.py
git commit -m "test(docs): define agent quick deployment contract"
```

---

### Task 2: Implement the Agent quick-deployment entry

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment/QUICKSTART.md`
- Create: `AGENTS.md`

**Interfaces:**
- Consumes: The authoritative execution rules in `docs/AGENT_DEPLOY.md` and the existing deterministic commands exposed by `deploy/cli.py`.
- Produces: One canonical universal prompt presented in two user-facing locations and one repository-level Agent instruction file.

- [ ] **Step 1: Add the README quick-deployment section before manual deployment**

Use the exact heading:

```markdown
## 快速部署：复制给 Agent（推荐）
```

Immediately state:

```markdown
你只需要做一件事：**复制下面整段内容，粘贴给能够操作终端和文件的 Agent。**
```

List Codex、Claude Code、Cursor、OpenCode、OpenCodex as examples, then include the canonical prompt between the marker comments.

- [ ] **Step 2: Use this canonical universal prompt verbatim in README and QUICKSTART**

```text
请帮我在当前 NAS 或 Linux Docker 主机上完整部署这个项目：

https://github.com/Inupedia/openclaw-nas-media-agent

你需要自行完成仓库克隆或更新、环境检查、配置生成、依赖部署、OpenClaw Skill 安装、服务初始化和安全验收。

开始前必须先读取并严格遵守仓库中的以下文件：

1. AGENTS.md
2. docs/AGENT_DEPLOY.md
3. docs/deployment/QUICKSTART.md
4. docs/deployment/SECURITY.md
5. docs/deployment/EXISTING_OPENCLAW.md
6. docs/deployment/QAS_LOGIN.md
7. docs/deployment/PROXY.md
8. docs/deployment/TROUBLESHOOTING.md

执行要求：

- 必须使用仓库内置的 deploy/cli.py 部署器，不得自行编造另一套部署流程；
- 当前目标是已有 Compose 管理的 OpenClaw 环境，不要承诺或尝试从空白主机自动安装 OpenClaw 本体；
- 先执行只读发现和部署计划，确认环境后再修改系统；
- 优先复用已有 OpenClaw、QAS、PanSou、aria2、Docker 网络和挂载目录；
- 不得猜测 NAS 路径、端口、账号、密钥或冲突目标；
- 需要缺失信息时直接向我提问；
- 遇到登录、扫码、验证码、冲突选择或危险操作确认时暂停并让我处理；
- 不得在聊天、日志、报告或 Git 提交中输出 Cookie、Token、密码和 RPC Secret；
- 未经我明确确认，不执行真实下载、整理入库或破坏性操作；
- 按 deploy/cli.py 输出的 status、nextAction 和错误码持续处理可安全自动修复的问题，直到 verify --level safe 完成；
- 最后向我报告部署状态、容器状态、路径映射、人工待办、验收结果和回滚方式。

现在开始部署。
```

- [ ] **Step 3: Rewrite QUICKSTART for ordinary users**

The page must explain prerequisites:

```text
- Agent can operate the target host terminal and files;
- Docker and Docker Compose are available;
- OpenClaw already runs under Docker Compose.
```

After the canonical prompt, explain that the user only needs to answer path questions and handle login/QR/captcha/conflict/danger gates. Link to `docs/deployment/EXISTING_OPENCLAW.md` for manual deployment.

- [ ] **Step 4: Add AGENTS.md**

Use concise repository-level instructions:

```markdown
# Deployment Instructions for Agents

When asked to install or deploy this repository, first read and follow `docs/AGENT_DEPLOY.md`.

Use `deploy/cli.py`; do not invent an alternative deployment procedure. Follow each JSON response's `status`, `nextAction`, and stable error code. Stop for user input when the deployer reports `manual_action_required`.

Never print or commit passwords, cookies, tokens, RPC secrets, or private endpoints. Do not perform real downloads, media-library writes, destructive permission changes, or other dangerous actions without explicit user confirmation.
```

- [ ] **Step 5: Run focused tests and confirm green state**

Run:

```bash
python3 -m unittest tests.test_readme_contract tests.test_agent_quickstart_contract tests.deploy.test_docs_examples -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit the documentation implementation**

```bash
git add README.md AGENTS.md docs/deployment/QUICKSTART.md
git commit -m "docs: add universal agent quick deployment"
```

---

### Task 3: Full validation and review

**Files:**
- Review: `README.md`
- Review: `AGENTS.md`
- Review: `docs/deployment/QUICKSTART.md`
- Review: `tests/test_readme_contract.py`
- Review: `tests/test_agent_quickstart_contract.py`

**Interfaces:**
- Consumes: All outputs from Tasks 1 and 2.
- Produces: A reviewable branch whose documentation contracts pass the complete repository suite.

- [ ] **Step 1: Run the full repository suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run documentation and syntax validation**

```bash
python3 -m unittest tests.deploy.test_docs_examples -v
python3 -m json.tool deploy/schemas/config.schema.json >/dev/null
python3 -m json.tool config/routing.json >/dev/null
git diff --check
```

Expected: all commands exit with status 0.

- [ ] **Step 3: Self-review against the specification**

Confirm:

```text
- quick section appears before manual section;
- user is explicitly told only to copy and paste the prompt;
- README and QUICKSTART prompt bodies are byte-for-byte identical;
- AGENTS.md points to docs/AGENT_DEPLOY.md and deploy/cli.py;
- manual deterministic command flow remains available;
- no tool-specific setup guide was added;
- no blank-host OpenClaw promise exists;
- no secret value or private endpoint was introduced.
```

- [ ] **Step 4: Open a pull request**

```text
Title: docs: make Agent quick deployment the primary path
Body: summarize the universal prompt, manual fallback, AGENTS.md contract, and test evidence.
```
