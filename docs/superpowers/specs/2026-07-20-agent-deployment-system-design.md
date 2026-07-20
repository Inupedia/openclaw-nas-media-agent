# Agent 全栈部署系统设计

- 状态：已确认，待实现计划
- 日期：2026-07-20
- 适用仓库：`Inupedia/openclaw-nas-media-agent`
- 首版正式支持：绿联 UGOS、标准 Linux Docker
- 实验性兼容：群晖、威联通、TrueNAS、Unraid

## 1. 背景

现有仓库已经具备较完整的 NAS 媒体搜索、候选预览、人工选版、QAS 转存、aria2 下载、校验和整理入库能力，也提供了面向 Agent 的部署说明和 QAS、PanSou、aria2 依赖 Compose 示例。

但现有部署方式仍属于“Agent 根据文档临场完成系统集成”，存在以下问题：

1. 默认假设 OpenClaw 已经正常运行，不能覆盖空白 NAS。
2. 依赖容器启动成功不代表 QAS 到 aria2 的真实下载链路已经配置完成。
3. PanSou 未形成代理配置、Telegram 连通性和数据源有效性的闭环验收。
4. 教父搜索虽然已经有 Playwright 客户端代码，但缺少 Chromium、登录态生成、续期和部署流程。
5. 配置分散在 Compose、`.env`、OpenClaw 配置、QAS 配置和 `routing.json` 中，Agent 需要自行推断。
6. `check-ready` 主要检查 aria2、路径和部分 QAS 状态，无法证明完整系统可用。
7. 多个镜像使用 `latest`，部署结果不可重复。
8. 缺少统一的计划、确认、备份、回滚和机器可读验收报告。

本设计将部署经验转化为仓库内置、可重复执行、可检测、可回滚的部署程序。Agent 的职责从“临场编写安装步骤”收敛为“收集必要配置、调用部署器、解释结果和请求确认”。

## 2. 目标

部署系统必须支持两种模式。

### 2.1 `existing-openclaw`

适用于已经运行 OpenClaw 的 NAS：

- 自动发现 OpenClaw 容器、workspace、配置文件和网络；
- 安装或更新本项目 Skill；
- 部署或安全复用 QAS、PanSou、aria2；
- 接入代理与教父搜索能力；
- 配置挂载、环境变量和固定 `mediactl` 执行权限；
- 完成无副作用验收；
- 用户确认后可完成真实小文件下载验收。

### 2.2 `full-stack`

适用于空白 NAS：

- 使用本仓库维护的完整 Docker Compose 部署 OpenClaw；
- 使用 OpenAI-Compatible 模型接口；
- 提供 OpenClaw Web／本地对话入口；
- 同时部署 QAS、PanSou、aria2、可选代理和 Jiaofu Runner；
- 安装并启用 Skill；
- 完成全栈初始化和验收。

### 2.3 用户参与边界

用户只负责：

- 提供或确认 NAS 真实目录；
- 提供模型 API Key、夸克账号相关凭据、代理凭据等必要秘密；
- 完成扫码、验证码、夸克登录或教父登录等不能可靠自动化的身份验证；
- 确认会修改系统或产生真实下载的操作。

除上述步骤外，配置生成、服务部署、网络接入、初始化、验证和回滚均由部署器完成。

## 3. 非目标

首版不包含：

- OpenClaw Telegram Bot、飞书或其他消息渠道；
- 对所有 NAS 平台提供正式兼容承诺；
- 自动绕过验证码、登录风控或网站安全挑战；
- 自动选择影视资源版本；
- 在完整验收中自动把测试文件写入正式媒体库；
- 为每个模型厂商编写独立适配器；
- 自动删除用户原有容器、真实下载文件或正式媒体库内容。

## 4. 核心设计原则

1. **唯一配置源**：`deploy/config.yaml` 是所有非敏感配置的唯一真实来源。
2. **秘密隔离**：Cookie、Token、API Key 和代理凭据放入独立 secret 文件。
3. **计划先行**：发现、计划与执行分离；没有有效计划和明确确认不得修改环境。
4. **确定性优先**：默认使用锁定版本，不使用未验证的 `latest`。
5. **幂等执行**：相同配置重复执行不产生不必要变更。
6. **失败可恢复**：每个写操作都必须有对应备份和逆操作。
7. **机器可读**：部署器所有命令输出单一 JSON 文档。
8. **安全不可绕过**：正式媒体库保护、secret 权限、allowlist 等安全阻断不能使用 `--force` 绕过。
9. **真实验收**：区分容器存活、组件可用和业务链闭环，禁止把容器绿色等同于部署成功。
10. **业务与部署分离**：部署代码位于 `deploy/`，不把 NAS 探测和 Compose 管理混入 `mediactl` 核心流程。

## 5. 方案选择

### 5.1 采用宿主机 Python 部署器

部署器以 Python 3 运行在 NAS 宿主机，直接访问：

- Docker CLI 与 Docker Compose；
- NAS 主机文件系统；
- 已有 OpenClaw Compose 和配置；
- 容器 inspect、网络、挂载和实际 UID/GID。

选择该方案的原因：

- 最适合绿联 UGOS 和标准 Linux；
- 更容易识别真实宿主机路径和已有容器；
- 更容易处理权限、ACL、备份和回滚；
- 核心逻辑可以独立测试；
- 后续可以将同一套 Python 代码封装为 Installer 容器，不维护第二套逻辑。

### 5.2 不采用纯 Shell 作为核心

Shell 仅可用于极薄的启动包装。YAML、JSON、计划签名、配置差异、秘密脱敏、事务回滚和版本适配必须由 Python 实现。

## 6. 仓库结构

建议新增：

```text
openclaw-nas-media-agent/
├── deploy/
│   ├── cli.py
│   ├── config.example.yaml
│   ├── versions.yaml
│   ├── schemas/
│   │   └── config.schema.json
│   ├── installer/
│   │   ├── config.py
│   │   ├── discovery.py
│   │   ├── preflight.py
│   │   ├── planning.py
│   │   ├── renderer.py
│   │   ├── executor.py
│   │   ├── verifier.py
│   │   ├── backup.py
│   │   ├── rollback.py
│   │   ├── redaction.py
│   │   ├── adapters/
│   │   │   ├── openclaw.py
│   │   │   ├── qas_v1.py
│   │   │   ├── pansou.py
│   │   │   ├── aria2.py
│   │   │   └── jiaofu.py
│   │   └── platforms/
│   │       ├── linux.py
│   │       └── ugos.py
│   ├── templates/
│   │   ├── compose.full-stack.yml.j2
│   │   ├── compose.dependencies.yml.j2
│   │   ├── compose.proxy.yml.j2
│   │   ├── compose.jiaofu.yml.j2
│   │   ├── openclaw.json.j2
│   │   ├── routing.json.j2
│   │   └── qas-aria2.json.j2
│   ├── secrets/
│   └── runtime/
│       ├── rendered/
│       ├── backups/
│       └── reports/
├── services/
│   └── jiaofu-runner/
└── tests/
    └── deploy/
```

`deploy/runtime/`、真实 `deploy/config.yaml` 和 `deploy/secrets/` 必须加入 `.gitignore`。

## 7. 命令接口

部署系统对外只暴露一个入口：

```bash
python3 deploy/cli.py init
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
python3 deploy/cli.py verify --level safe
python3 deploy/cli.py verify --level full --confirmed
python3 deploy/cli.py rollback --deployment-id DEPLOYMENT_ID --confirmed
python3 deploy/cli.py versions check
```

每个命令只向标准输出写一个 JSON 文档。诊断日志写入标准错误或报告文件，并经过脱敏。

统一输出字段：

```json
{
  "ok": true,
  "status": "ready_for_apply",
  "nextAction": "request_confirmation",
  "data": {},
  "warnings": [],
  "errors": []
}
```

## 8. 配置模型

### 8.1 唯一配置源

`deploy/config.yaml` 示例：

```yaml
deployment:
  mode: existing-openclaw
  platform: ugos
  project_dir: /volume1/docker/openclaw-media
  timezone: Asia/Shanghai
  allow_reuse_existing_services: true

nas:
  downloads_dir: /volume2/downloads
  libraries:
    movie: /volume3/临时影视/Movie
    drama: /volume2/影视/Drama
    anime: /volume2/影视/Anime
    documentary: /volume2/影视/Documentary
    show: /volume2/影视/Shows
    other: /volume2/影视/Others
  organizing_dir: /volume3/临时影视/.openclaw-organizing

openclaw:
  existing:
    container_name: auto
    workspace_host_dir: auto
    config_host_path: auto
  full_stack:
    web_port: 3000
    model:
      type: openai-compatible
      base_url: https://api.example.com/v1
      model: model-name
      api_key_secret: model_api_key

qas:
  deploy: auto
  port: 5005
  username: admin
  password_secret: qas_webui_password
  api_token_secret: qas_token
  quark_cookie_secret: quark_cookie
  aria2_integration: auto

aria2:
  deploy: auto
  rpc_port: 6800
  rpc_secret: aria2_rpc_secret
  uid: auto
  gid: auto

pansou:
  enabled: true
  deploy: auto
  port: 8888
  channels:
    - tgsearchers3
  plugins: []
  proxy:
    mode: existing
    url_secret: pansou_proxy_url

jiaofu:
  enabled: true
  storage_state_secret: jiaofu_storage_state
  max_candidates: 20

verification:
  safe: true
  full_test_share_url_secret: full_test_share_url
  allow_real_download: false
```

### 8.2 `auto` 解析规则

`auto` 必须遵循确定规则：

1. 查找匹配的现有容器、路径或配置；
2. 检查镜像、端口、挂载、网络和名称；
3. 只有唯一且可信的结果才自动选择；
4. 多个候选、冲突或不完整结果必须停止；
5. 候选写入 `plan.json`，由用户明确选择；
6. 不得静默覆盖现有服务。

### 8.3 交互式向导

`init` 提供交互式向导，但只生成或修改 `config.yaml` 和 secrets 模板，不直接部署。Agent 也可以直接生成相同配置文件。两种入口必须进入同一套 schema 验证和部署流程。

## 9. 秘密管理

敏感内容存放于：

```text
deploy/secrets/
├── model_api_key
├── qas_webui_password
├── qas_token
├── quark_cookie
├── aria2_rpc_secret
├── pansou_proxy_url
├── jiaofu_storage_state.json
└── full_test_share_url
```

要求：

- `deploy/secrets/` 权限为 `0700`；
- 普通 secret 文件权限为 `0600`；
- storage state JSON 权限为 `0600` 且必须可解析；
- 容器通过只读挂载或启动时读取，不复制到公开配置；
- secrets 不进入 Git、备份包、计划差异或验收报告；
- 所有异常必须经过 secret 值替换和字段级脱敏；
- 备份只记录 secret 文件是否存在及哈希，不保存内容。

首版不依赖 Docker Swarm secrets 或特定 NAS 密钥管理器，以保证 UGOS 和标准 Linux 一致可用。

## 10. 版本管理

新增 `deploy/versions.yaml`：

```yaml
schema_version: 1
openclaw:
  image: example/openclaw:1.2.3
  digest: sha256:...
qas:
  image: cp0204/quark-auto-save:0.x.y
  digest: sha256:...
  config_adapter: qas_v1
pansou:
  image: ghcr.io/fish2018/pansou:2026.x
  digest: sha256:...
aria2:
  image: p3terx/aria2-pro:2026.x
  digest: sha256:...
jiaofu_runner:
  image: ghcr.io/inupedia/jiaofu-runner:0.1.0
  digest: sha256:...
```

规则：

- 默认使用经过验证的 tag 和 digest；
- `config.yaml` 可以显式覆盖版本，但计划中必须给出“未验证版本”警告；
- `versions check` 只检查更新，不自动修改；
- 更新版本锁前必须完成测试环境 `safe` 和维护者 `full` 验收；
- QAS 等配置结构变化通过新 adapter 处理，不在旧 adapter 中无限增加条件分支。

## 11. 执行流程

```text
init
  ↓
discover
  ↓
plan
  ↓
用户确认
  ↓
apply
  ↓
verify safe
  ↓
可选 verify full
```

### 11.1 `discover`

只读收集：

- NAS 平台、CPU 架构、文件系统和可用空间；
- Docker、Compose 版本和管理权限；
- OpenClaw、QAS、PanSou、aria2 等现有容器；
- 网络、端口、卷和绑定挂载；
- OpenClaw workspace 和配置文件；
- 目标媒体目录是否为真实挂载；
- aria2 实际 UID/GID；
- secrets 是否存在及权限是否符合要求。

发现阶段不得安装软件、创建目录或修改权限。

### 11.2 `plan`

- 校验 schema；
- 解析 `auto`；
- 计算 Compose、配置、网络、挂载和权限差异；
- 生成备份范围和回滚步骤；
- 生成唯一 `planId`；
- 对配置摘要和发现结果计算哈希；
- 默认设置有限有效期；
- 标记所有副作用和用户确认点。

### 11.3 `apply`

必须带 `--plan-id` 和 `--confirmed`。执行前重新校验：

- 配置哈希未变化；
- secrets 文件状态未变化；
- 关键容器和配置未发生漂移；
- 计划未过期；
- 备份成功。

不符合任何条件时，计划失效，必须重新生成。

### 11.4 幂等规则

- 相同目录不重复创建；
- 配置未变化不重启服务；
- 容器配置一致不重建；
- Skill 已是目标版本不重复安装；
- 已接入网络不重复操作；
- 检测到用户手工修改时显示差异并重新计划；
- 无法安全合并时停止，不强制覆盖。

## 12. OpenClaw 初始化

### 12.1 `existing-openclaw`

部署器必须识别：

- OpenClaw 容器和镜像；
- workspace 主机路径及容器路径；
- `openclaw.json` 或当前版本配置位置；
- Compose 项目；
- 当前网络；
- Skill 配置结构；
- exec 安全策略和 allowlist。

增量修改流程：

1. 备份 Compose、OpenClaw 配置和现有 Skill；
2. 将 OpenClaw 接入共享网络；
3. 添加下载目录和媒体库挂载；
4. 安装或更新 `resource-download-agent`；
5. 写入 Skill 环境变量；
6. 只允许固定绝对路径的 `mediactl`；
7. 按版本适配器重载或重启；
8. 验证 Skill 被发现且能执行固定命令。

不能识别配置结构时返回 `manual_action_required` 或 `failed`，不得猜测写入。

### 12.2 `full-stack`

完整 Compose 必须包含：

- OpenClaw；
- workspace 与配置持久化；
- OpenAI-Compatible 模型地址、模型名和 API Key secret；
- Web／本地对话入口；
- Skill 目录；
- 下载目录和媒体库挂载；
- `openclaw-media` 网络；
- 固定 `mediactl` allowlist；
- 健康检查。

首版只保证 Web／本地对话入口，不配置其他消息渠道。

## 13. QAS 初始化

采用三级策略，按照稳定性从高到低依次执行。

### 13.1 配置文件适配器

根据锁定版本和 adapter 写入：

- WebUI 用户名和密码；
- API Token；
- 夸克 Cookie；
- aria2 RPC 地址；
- aria2 RPC Secret；
- aria2 下载目录；
- aria2 插件启用状态。

写入前备份，写入后重启并重新读取验证。

### 13.2 API 初始化

当前版本提供稳定配置 API 时：

1. 读取当前配置；
2. 生成脱敏差异；
3. 写入配置；
4. 重新读取并逐字段核验；
5. 执行 QAS 到 aria2 的链路检查。

报告只能显示 `configured`、`missing` 或 `invalid`，不得显示真实凭据。

### 13.3 浏览器自动化兜底

配置文件和 API 均不适配时：

1. 启动临时浏览器；
2. 打开 NAS 本地 QAS WebUI；
3. 用户完成扫码、验证码或登录；
4. 自动填写非身份验证配置；
5. 保存结果并关闭临时浏览器；
6. 重新执行 QAS 验收。

需要用户操作时返回：

```json
{
  "ok": false,
  "status": "manual_action_required",
  "nextAction": "complete_qas_login"
}
```

QAS 容器存活但 Cookie、Token 或 aria2 插件未配置时，不能标记为 `ready`。

## 14. PanSou 与代理

支持：

```yaml
proxy:
  mode: none
```

```yaml
proxy:
  mode: existing
  url_secret: pansou_proxy_url
```

```yaml
proxy:
  mode: managed
  profile: proxy
```

- `none`：当前网络可直接访问 Telegram；
- `existing`：复用用户提供的 SOCKS5 或 HTTP 代理；
- `managed`：启用仓库内置 `proxy` Compose profile，用户仍需提供合法节点或订阅配置。

部署器根据代理类型设置 PanSou 支持的 `PROXY`、`HTTP_PROXY` 或 `HTTPS_PROXY`，不得将代理凭据写入报告。

PanSou 验收必须区分：

1. 容器健康；
2. API 可达；
3. 频道配置生效；
4. Telegram 数据源实际可用；
5. 代理失败是否能被明确定位。

PanSou 或代理是可选发现能力。失败时核心系统可标记为 `degraded`，但不能在启用该组件时报告整体 `ready`。

## 15. Jiaofu Runner

### 15.1 架构变更

将教父 Playwright 运行环境从 OpenClaw／`mediactl` 进程中分离为独立内部服务：

```text
Jiaofu Runner
├── Python
├── Playwright
├── Chromium
├── storage state
└── internal HTTP API
```

原因：

- 不污染 OpenClaw 镜像；
- Chromium 系统依赖和版本可以独立锁定；
- amd64／arm64 兼容可以单独测试；
- 登录态独立持久化；
- 爬虫异常不会拖垮 OpenClaw；
- 后续可以增加其他浏览器发现源。

### 15.2 内部 API

首版内部接口：

```text
GET  /health
GET  /session/status
POST /session/login/start
POST /search
```

`POST /search` 请求：

```json
{
  "query": "作品名",
  "maxCandidates": 20
}
```

返回只包含标题、规范化夸克分享链接和安全状态，不返回浏览器 Cookie 或原始页面内容。

### 15.3 Skill 兼容

`mediactl` 增加 `JIAOFU_BASE_URL` 支持，通过内部 HTTP API调用 Runner。为兼容已有部署，可在一个过渡版本中保留本地 `JIAOFU_STORAGE_STATE` 模式，但新部署默认使用 Runner，后续移除本地 Playwright 路径。

### 15.4 登录流程

- 检查 Chromium 可启动；
- storage state 不存在或过期时进入 `manual_action_required`；
- 用户完成一次登录；
- Runner 保存 storage state；
- 执行测试查询；
- 确认结果中存在合法夸克链接；
- 登录过期返回 `nextAction=refresh_jiaofu_login`。

Jiaofu 是可选组件。失败时系统可以 `degraded`，不能阻断直接分享链接、QAS 或 PanSou。

## 16. aria2 与权限

部署器必须读取容器实际运行身份，不假定一定是 `nobody:nogroup`：

- `docker inspect`；
- 容器内 `id`；
- 目标目录 owner、group、mode；
- 平台 ACL 能力。

权限策略顺序：

1. UID/GID 对齐；
2. 共享用户组；
3. POSIX ACL；
4. 仅对托管下载根和 `.incoming` 使用宽松权限；
5. 永不递归修改正式媒体库。

计划中必须列出权限差异、理由和影响。未经确认不得修改。

OpenClaw 和 aria2 必须挂载同一宿主机下载目录，aria2 内部路径固定为 `/nas/downloads`，OpenClaw 内部路径与生成的 `routing.json` 一致。

## 17. 分层验收

### L0：静态配置

- YAML、JSON、Compose 语法；
- schema；
- secrets 权限；
- 镜像版本与架构；
- 端口冲突；
- 主机路径和挂载真实性；
- 配置中的正式媒体库保护关系。

### L1：容器健康

检查所有已启用组件的健康检查：

- OpenClaw；
- QAS；
- PanSou；
- aria2；
- Jiaofu Runner；
- 可选代理。

不以单纯 `docker ps` 作为通过条件。

### L2：内部网络

验证：

- OpenClaw → QAS；
- OpenClaw → PanSou；
- OpenClaw → aria2 RPC；
- OpenClaw → Jiaofu Runner；
- PanSou → Telegram／代理；
- QAS → aria2。

### L3：组件功能

- QAS Token 有效；
- QAS Cookie 有效；
- QAS aria2 插件配置有效；
- PanSou 可返回 Telegram 来源；
- Jiaofu 可返回合法夸克链接；
- aria2 写入共享目录成功；
- OpenClaw 发现 Skill；
- `mediactl check-ready` 成功。

### L4：安全验收

- 正式媒体库不能作为下载目标；
- protected roots 生效；
- 下载和整理均需明确确认；
- OpenClaw 只能调用固定 `mediactl`；
- 日志和报告不泄露 secret；
- 过期或配置变化后的计划不能执行；
- 受保护内容删除请求被拒绝。

### L5：`safe` 业务验收

默认执行，不产生真实转存：

```text
OpenClaw 对话
  → mediactl
  → 本地检索
  → 远端只读搜索
  → 候选预览
  → 目录树
  → 下载计划
  → 停止
```

若搜索源没有稳定测试结果，`safe` 可使用用户提供的合法分享链接完成预览、目录树和计划验证，但不得执行下载。

### L6：`full` 业务验收

只有用户显式确认且提供合法测试分享链接时执行：

```text
导入分享链接
  → 预览
  → 选择测试文件
  → 下载计划
  → QAS 转存
  → aria2 下载
  → .incoming
  → validate
  → .ready
  → organize plan
  → 停止
```

默认不执行 `organize execute`，测试文件不进入正式媒体库。

## 18. 状态模型

部署器最终状态只能是：

- `ready`：所有启用组件及对应验收通过；
- `degraded`：核心链路可用，但可选组件异常；
- `manual_action_required`：等待登录、验证码或用户选择；
- `failed`：核心组件、数据路径或安全验收失败；
- `rolled_back`：失败后已经恢复原配置。

错误级别：

- `warning`：不影响核心部署；
- `degraded`：可选能力不可用；
- `blocking`：核心链路不可用；
- `security_block`：安全约束不满足，立即停止。

`security_block` 不允许强制绕过。

## 19. 备份与回滚

### 19.1 事务式执行

```text
锁定计划
  → 再校验
  → 创建时间戳备份
  → 渲染临时配置
  → 静态验证
  → 分组件应用
  → safe 验收
  → 提交部署结果
```

每个写操作记录逆操作：

```json
{
  "action": "update_file",
  "target": "/path/openclaw.json",
  "rollback": {
    "action": "restore_file",
    "backup": "backups/20260720/openclaw.json"
  }
}
```

### 19.2 自动回滚范围

- OpenClaw 配置和 Compose；
- QAS 配置；
- Skill 旧版本；
- 本次新建且未承载用户数据的容器；
- 本次新增的网络连接；
- 本次修改的托管目录权限；
- `routing.json`；
- 本项目状态数据库。

### 19.3 不自动删除

- 用户原有容器；
- 真实下载文件；
- 正式媒体库内容；
- 用户已有网络和数据卷；
- 用户提供的代理配置和凭据。

已经产生真实下载时，回滚只恢复配置并报告遗留文件位置。

## 20. 报告文件

每次运行生成：

```text
deploy/runtime/
├── plan.json
├── rendered/
├── backups/<deployment-id>/
└── reports/
    ├── discovery.json
    ├── preflight.json
    ├── apply.json
    └── verify.json
```

报告必须提供：

- 组件状态；
- 复用或新建决策；
- 路径映射；
- 网络关系；
- 脱敏配置状态；
- 验收层级与结果；
- 备份位置；
- 回滚命令；
- 明确 `nextAction`。

## 21. 测试策略

新增：

```text
tests/deploy/
├── test_config.py
├── test_discovery.py
├── test_preflight.py
├── test_plan.py
├── test_renderer.py
├── test_redaction.py
├── test_backup.py
├── test_rollback.py
├── test_openclaw_adapter.py
├── test_qas_bootstrap.py
├── test_pansou_proxy.py
├── test_aria2_permissions.py
├── test_jiaofu_runner.py
└── test_verifier.py
```

### 21.1 单元测试

- schema 和配置默认值；
- `auto` 决策；
- 计划签名与失效；
- 脱敏；
- 权限决策；
- 版本适配；
- 备份和逆操作；
- 状态模型。

### 21.2 Compose 集成测试

使用临时目录和模拟服务验证：

- 模板渲染；
- 容器网络；
- 健康检查；
- 路径映射；
- 配置挂载；
- 重复 apply 的幂等性；
- 中途失败后的回滚。

### 21.3 端到端测试

在受控测试环境验证完整组件组合。CI 默认不接触真实 Cookie、夸克账号、代理节点或教父登录态。真实 `full` 验收由维护者手动触发并使用专用测试账户和合法测试资源。

## 22. 文档重构

README 只保留主入口：

```bash
git clone https://github.com/Inupedia/openclaw-nas-media-agent.git
cd openclaw-nas-media-agent
python3 deploy/cli.py init
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
```

部署文档拆分：

```text
docs/deployment/
├── QUICKSTART.md
├── EXISTING_OPENCLAW.md
├── FULL_STACK.md
├── QAS_LOGIN.md
├── JIAOFU_LOGIN.md
├── PROXY.md
├── TROUBLESHOOTING.md
└── SECURITY.md
```

README 中对能力的表述应为：

> Agent 读取配置并调用仓库内置部署器完成安装、初始化、验收和回滚；只有扫码、验证码及危险操作确认需要用户参与。

## 23. 分阶段交付

### 第一阶段：`existing-openclaw`

- 配置 schema、向导和 secret 管理；
- UGOS／Linux 发现；
- 计划、备份、回滚和脱敏；
- 依赖 Compose 和锁定版本；
- 已有 OpenClaw 适配；
- QAS、PanSou、aria2 初始化；
- `safe` 验收；
- 现有本地 Playwright 模式仍可兼容。

### 第二阶段：Jiaofu Runner

- 独立容器和 HTTP API；
- 登录态初始化与续期；
- `mediactl` 接入；
- amd64／arm64 验证。

### 第三阶段：`full-stack`

- OpenClaw 完整 Compose；
- OpenAI-Compatible 模型配置；
- Web／本地对话入口；
- 从空白 NAS 端到端部署。

### 第四阶段：增强与扩展

- Installer 容器入口；
- 更多 NAS 平台正式适配；
- 显式版本升级工具；
- 更多发现服务 Runner。

## 24. 验收标准

首版 `existing-openclaw` 被视为完成必须同时满足：

1. 新手可以通过向导生成有效配置和 secrets 模板；
2. Agent 可以只根据 `config.yaml` 调用部署器完成计划和执行；
3. 不要求 Agent 临场编辑 Compose、OpenClaw JSON、QAS 配置或 `routing.json`；
4. 重复执行相同配置不产生不必要重启或容器重建；
5. QAS 容器存活但 Cookie／aria2 插件未配置时验收失败；
6. PanSou 启用但 Telegram／代理不可用时状态为 `degraded`；
7. aria2 写入目录映射可通过真实探针验证；
8. OpenClaw 能在对话中加载 Skill 并调用固定 `mediactl`；
9. `safe` 验收不会创建真实下载；
10. `full` 验收必须明确确认，并停在整理计划；
11. 所有报告不包含真实 Secret；
12. 任一核心失败可以恢复到执行前配置；
13. 正式媒体库内容在部署、验收和回滚中不会被删除或覆盖。

`full-stack` 被视为完成还必须满足：

1. 空白 UGOS 或标准 Linux Docker 主机可以部署 OpenClaw 和全部核心依赖；
2. OpenAI-Compatible 模型连接成功；
3. Web／本地对话入口可用；
4. OpenClaw 可以加载本项目 Skill；
5. `safe` 和维护者 `full` 验收通过；
6. 整个流程不需要用户手工编辑配置文件，只需要填写配置、秘密和完成必要登录。

## 25. 主要风险及缓解

### QAS 配置结构变化

通过锁定版本、adapter 和回读验证解决。不能识别的版本不自动修改。

### OpenClaw 配置版本差异

通过版本适配器、备份和失败停止解决。首版正式支持范围必须记录经过验证的 OpenClaw 版本。

### 网站登录风控

不尝试绕过；返回 `manual_action_required`，允许用户完成登录后继续。

### UGOS 权限与 ACL 差异

通过平台适配器、真实 UID/GID 探测和受限权限变更解决，正式媒体库永不递归 chmod。

### 代理与 Telegram 不稳定

将 PanSou 视为可降级组件，提供独立连通性报告，不把空结果误报为成功。

### 浏览器镜像架构支持

Jiaofu Runner 分别验证 amd64 和 arm64；不支持的架构自动关闭该可选组件并给出明确状态。

## 26. 设计结论

部署能力必须从“README 中的建议步骤”升级为“仓库内置的确定性部署程序”。

目标不是让 Agent 更聪明地猜，而是让 Agent 只做以下事情：

1. 收集用户必须提供的信息；
2. 调用 `init`、`discover`、`plan`、`apply` 和 `verify`；
3. 展示变更和请求确认；
4. 根据结构化状态指导用户完成一次登录或处理明确错误。

当本设计完成后，新手使用系统的体验应当是：填写配置和 secrets、完成必要登录、确认部署和真实下载，其余安装、初始化、验证及回滚由部署器闭环完成。