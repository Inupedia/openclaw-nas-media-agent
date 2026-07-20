# Agent 部署手册

本手册的首要规则是：**Agent 必须调用仓库内置部署器，不得根据文档自行拼装另一套部署流程。**

```bash
python3 deploy/cli.py init
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
python3 deploy/cli.py verify --level safe
```

所有命令输出一个 JSON 文档。Agent 根据 `status`、`nextAction` 和稳定错误码推进；遇到 `manual_action_required` 时停止并请求用户完成明确动作。下面的检查项用于理解和排错，不替代部署器。

本文不是给用户逐行照抄的安装教程，而是给具有 NAS 与 Docker 管理权限的部署 Agent 使用的执行契约。

## 目标

完成以下结果：

1. OpenClaw 能发现并加载 `resource-download-agent` Skill。
2. `mediactl` 能访问 QAS、可选 PanSou 和 aria2 RPC。
3. OpenClaw 与 aria2 能访问同一份下载目录。
4. 下载只进入暂存区，正式媒体库只在用户确认整理后写入。
5. 不破坏现有 Docker 项目、媒体目录、凭据和网络配置。

## 强制执行原则

- 先检查、再计划、后执行。
- 修改 Compose、环境变量、目录权限或配置文件之前，先向用户展示变更。
- 已存在的 QAS、PanSou 或 aria2 优先复用，不重复创建同名服务。
- 不猜测 NAS 卷路径，不照抄 `/volume2`、`/volume3`、`/volume4`。
- 不在对话、日志、补丁或 Git 提交中输出真实 Cookie、Token、RPC Secret。
- 不把正式媒体库配置为 QAS 或 aria2 的直接下载目标。
- 不删除、覆盖、清理或移动受保护媒体库已有内容。
- 未获得用户明确确认，不启动真实下载和整理入库。

## 阶段 A：只读发现

### A1. 主机与 Docker

收集并报告：

```bash
uname -a
uname -m
docker version
docker compose version || docker-compose version
docker info --format '{{json .}}'
```

识别：

- NAS 品牌与系统；
- CPU 架构：`amd64` 或 `arm64`；
- Docker Compose 命令；
- 当前用户是否具备 Docker 管理权限；
- 可用磁盘与目标卷文件系统。

不得在此阶段安装软件。

### A2. OpenClaw

定位 OpenClaw 容器、Compose 项目与 workspace：

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect <openclaw-container>
```

确认：

- 主机 workspace 路径；
- 容器内 workspace 路径；
- Skill 安装目录；
- OpenClaw 配置文件位置；
- 当前 exec 安全模式和 allowlist；
- OpenClaw 容器网络。

### A3. 依赖服务

检查现有容器、Compose 项目和端口：

```bash
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls
docker volume ls
```

识别以下服务：

| 服务 | 识别重点 |
|---|---|
| QAS | 镜像、Web/API 端口、配置目录、下载插件配置、API Token 获取方式 |
| PanSou | 镜像、API 端口、是否与 OpenClaw 同网络 |
| aria2 | RPC 端口、RPC Secret、下载卷、运行用户、RPC 是否启用 |

不得通过打印容器完整环境变量的方式泄露凭据。只报告“已配置/未配置”。

### A4. 路径确认

必须向用户确认以下主机真实路径：

- 下载根目录；
- 电影库；
- 电视剧库；
- 动画库；
- 纪录片库；
- 综艺库；
- 其他媒体库；
- 跨盘整理临时目录；
- OpenClaw workspace。

检查目录是否是真实挂载，而非容器内误创建目录。

### A5. 输出部署计划

计划必须包含：

- 复用哪些容器；
- 新增哪些容器；
- 容器网络；
- 端口映射；
- 主机路径与容器路径映射表；
- 将修改的文件；
- 备份位置；
- 权限处理方式；
- 验收命令；
- 回滚命令。

用户确认前停止。

## 阶段 B：执行部署

### B1. 备份

至少备份：

- OpenClaw Compose 文件；
- OpenClaw 主配置；
- 本项目旧 Skill 目录；
- `config/routing.json`；
- 状态数据库；
- 将要修改的 QAS/aria2 Compose 文件。

备份文件使用时间戳目录，不覆盖旧备份。

### B2. 安装或更新 Skill

新安装：

```bash
cd <openclaw-workspace-host>/skills
git clone https://github.com/Inupedia/openclaw-nas-media-agent.git resource-download-agent
chmod 0755 resource-download-agent/bin/mediactl
mkdir -p <openclaw-workspace-host>/data/resource-download-agent
```

更新：

```bash
cd <openclaw-workspace-host>/skills/resource-download-agent
git status --short
git pull --ff-only
```

若存在本地修改，不得强制覆盖；先报告冲突并停止。

### B3. 部署依赖容器

若 QAS、PanSou、aria2 缺失，可基于：

```text
deploy/docker-compose.dependencies.yml
deploy/.env.dependencies.example
```

创建独立 Compose 项目。

要求：

- 密钥只放在 `.env` 或 NAS 密钥管理中；
- `.env` 权限至少为 `0600`；
- 服务名称和端口冲突时调整，不删除现有容器；
- QAS 与 aria2 的具体联动设置，以当前 QAS 版本说明为准；
- PanSou 仅作为候选发现服务；
- 不将管理端口暴露到公网；
- 能使用内部 Docker 网络时，不额外映射 RPC 端口到所有网卡。

### B4. 下载目录映射

同一个主机下载目录必须满足：

| 角色 | 容器内路径 |
|---|---|
| OpenClaw / `mediactl` | 与 `downloads.agent_root` 一致 |
| aria2 | `/nas/downloads` |

例如主机目录为 `/mnt/pool/downloads`：

```yaml
# OpenClaw
volumes:
  - /mnt/pool/downloads:/mnt/pool/downloads

# aria2
volumes:
  - /mnt/pool/downloads:/nas/downloads
```

不要让 QAS 或 aria2 直接写正式影视库。

### B5. 初始化暂存目录

```bash
mkdir -p <downloads>/.incoming <downloads>/.ready <downloads>/.quarantine
```

优先使用正确的 UID/GID 和组权限。只有无法协调容器运行用户时，才对这三个托管暂存目录使用宽松权限；不得递归修改正式媒体库权限。

### B6. 修改 `routing.json`

根据真实路径设置：

```json
{
  "downloads": {
    "host_root": "<host-download-root>",
    "agent_root": "<openclaw-visible-download-root>",
    "aria2_root": "/nas/downloads",
    "staging_root": "<agent-download-root>/.incoming",
    "ready_root": "<agent-download-root>/.ready",
    "quarantine_root": "<agent-download-root>/.quarantine"
  }
}
```

同时更新所有媒体类型的 `final_root`、`protected_roots` 和 `organizing_root`。

要求：

- `protected_roots` 覆盖所有正式媒体库上级目录；
- `organizing_root` 位于目标库所在文件系统或已正确挂载；
- 所有正式目标父目录必须预先存在；
- 修改后检查 JSON 语法。

### B7. 配置 Skill 环境变量

至少配置：

```dotenv
QAS_BASE_URL=http://<qas-service>:<port>
QAS_TOKEN=<secret>
PANSOU_BASE_URL=http://<pansou-service>:8888
PANSOU_MAX_CANDIDATES=50
ARIA2_RPC_URL=http://<aria2-service>:6800/jsonrpc
ARIA2_RPC_SECRET=<secret>
RESOURCE_AGENT_STATE_DB=/root/.openclaw/workspace/data/resource-download-agent/state.db
```

可写入 OpenClaw 的 Skill env 配置或容器环境。修改后按 OpenClaw 当前版本要求重启/重载 gateway。

### B8. 收紧 OpenClaw 执行权限

只允许固定绝对路径：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl
```

要求：

- exec security 使用 allowlist；
- 不允许任意 shell；
- 不允许 `bash -lc`、管道、`&&` 包装；
- 不允许 `rm`、`curl`、任意 Python、sudo；
- 执行确认逻辑仍由 Skill 和 `mediactl` 双重约束。

## 阶段 C：验证

### C1. 静态检查

```bash
python3 -m json.tool config/routing.json >/dev/null
python3 -m unittest discover -s tests -v
```

### C2. 就绪检查

在 OpenClaw 实际运行环境执行固定 CLI：

```bash
<absolute-mediactl-path> check-ready
```

必须返回可解析 JSON，且 `nextAction` 为 `ready`。失败时只按错误提示修正配置，不绕过安全检查。

### C3. 只读搜索验收

执行一次明确的只预览请求：

```bash
<absolute-mediactl-path> search "测试作品" --media-type anime
```

确认：

- 没有创建 `.incoming` 下载任务；
- 没有触发整理；
- 不输出底层分享链接、Cookie、Token 或完整服务响应；
- 候选由用户选择。

### C4. 对话验收

向 OpenClaw 发送：

```text
搜索《测试作品》动画，先检查本地，只预览，不要下载。
```

确认 Agent 调用的是固定 `mediactl`，没有调用通用 shell 或 Web 搜索替代流程。

## 最终报告模板

```markdown
## 部署结果

- Skill 主机路径：
- Skill 容器路径：
- OpenClaw 容器：
- QAS：复用/新建，状态正常/异常
- PanSou：复用/新建/未启用，状态正常/异常
- aria2：复用/新建，状态正常/异常

## 路径映射

| 用途 | 主机 | OpenClaw | aria2 |
|---|---|---|---|
| 下载根 | ... | ... | /nas/downloads |
| 正式媒体库 | ... | ... | 不挂载/只读 |

## 验收

- 单元测试：
- check-ready：
- 只读搜索：
- allowlist：
- 受保护目录：

## 备份与回滚

- 备份目录：
- 回滚命令：
```

## 故障停止条件

遇到以下情况立即停止，不自行绕过：

- 无法确定真实卷路径；
- 正式媒体库挂载缺失；
- 发现同名容器或端口冲突但无法判断用途；
- QAS/aria2 凭据缺失；
- OpenClaw 无法配置固定命令 allowlist；
- `routing.json` 指向不存在或不可确认的正式目录；
- 测试失败；
- `check-ready` 未返回 `ready`；
- 用户未确认会产生副作用的操作。
