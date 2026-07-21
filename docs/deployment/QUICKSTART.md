# 快速部署：复制给 Agent

这条路径适合绝大多数用户。

你只需要做一件事：**复制下面整段内容，粘贴给能够操作目标主机终端和文件的 Agent。**

可使用 Codex、Claude Code、Cursor、OpenCode、OpenCodex，或其他具备终端和文件操作能力的编码 Agent。后续只有在 Agent 询问路径、登录、扫码、验证码、冲突选择或危险操作确认时，你才需要参与。

## 前置条件

- Agent 能访问并操作目标 NAS 或 Linux 主机的终端和文件；
- 主机已经安装 Docker 和 Docker Compose；
- OpenClaw 已经通过 Docker Compose 运行；
- 当前版本不会从空白主机自动安装 OpenClaw 本体。

## 复制下面整段内容

<!-- AGENT_QUICK_DEPLOY_PROMPT_START -->
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
<!-- AGENT_QUICK_DEPLOY_PROMPT_END -->

## 你可能需要处理的情况

Agent 会尽量自动完成环境检查、部署、配置和 `safe` 验收。以下情况会暂停并向你提问：

- 真实 NAS 路径或媒体库路径无法唯一确定；
- QAS 或来源网站需要登录、扫码或验证码；
- 发现多个可复用的 OpenClaw、QAS、PanSou 或 aria2 实例；
- 需要修改目录权限；
- 需要执行真实下载、`full` 验收或整理入库。

## 手动部署

不使用 Agent 时，请按照 [已有 OpenClaw 模式](EXISTING_OPENCLAW.md) 自己执行：

```bash
git clone https://github.com/Inupedia/openclaw-nas-media-agent.git
cd openclaw-nas-media-agent
python3 deploy/cli.py init
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
python3 deploy/cli.py verify --level safe
```

完整验收只生成整理计划，不执行正式入库：

```bash
python3 deploy/cli.py verify --level full --confirmed
```
