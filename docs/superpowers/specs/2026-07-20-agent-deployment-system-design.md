# Agent 全栈部署系统设计

- 状态：已确认，待实现计划
- 日期：2026-07-20
- 适用仓库：`Inupedia/openclaw-nas-media-agent`
- 首版正式支持：绿联 UGOS、标准 Linux Docker
- 实验性兼容：群晖、威联通、TrueNAS、Unraid

## 1. 背景

现有仓库已经具备 NAS 本地检索、远端候选发现、候选预览、人工选版、QAS 转存、aria2 下载、校验和整理入库能力，也提供了面向 Agent 的部署说明及 QAS、PanSou、aria2 依赖 Compose 示例。

但当前部署方式仍属于“Agent 根据文档临场完成系统集成”，主要问题是：

1. 默认假设 OpenClaw 已经正常运行，不能覆盖空白 NAS。
2. 容器启动成功不代表 QAS 到 aria2 的真实下载链路已经配置完成。
3. PanSou 没有形成代理配置、Telegram 连通性和数据源有效性的闭环验收。
4. 教父搜索已有 Playwright 客户端代码，但缺少 Chromium、登录态生成、续期和部署流程。
5. 配置分散在 Compose、环境变量、OpenClaw 配置、QAS 配置和 `routing.json` 中。
6. `check-ready` 不能证明 OpenClaw、QAS、PanSou、代理和教父组成的完整系统可用。
7. 多个镜像使用 `latest`，部署结果不可重复。
8. 缺少统一计划、确认、备份、回滚和机器可读验收报告。

本设计将部署经验转化为仓库内置、可重复执行、可检测、可回滚的部署程序。Agent 不再临场编写安装步骤，而是收集必要配置、调用部署器、展示变更、请求确认并解释结构化结果。

## 2. 目标与范围

部署系统必须支持两种模式。

### 2.1 `existing-openclaw`

适用于已经运行 OpenClaw 的 NAS：

- 自动发现 OpenClaw 容器、workspace、配置文件和网络；
- 安装或更新本项目 Skill；
- 部署或安全复用 QAS、PanSou、aria2；
- 接入可选代理和教父搜索能力；
- 配置挂载、环境变量和固定 `mediactl` 执行权限；
- 完成无真实下载的 `safe` 验收；
- 用户明确确认后完成真实小文件 `full` 验收。

### 2.2 `full-stack`

适用于空白 NAS：

- 使用本仓库维护的完整 Docker Compose 部署 OpenClaw；
- 使用通用 OpenAI-Compatible 模型接口；
- 提供 OpenClaw Web／本地对话入口；
- 同时部署 QAS、PanSou、aria2、可选代理和 Jiaofu Runner；
- 安装并启用 Skill；
- 完成全栈初始化和验收。

### 2.3 用户参与边界

用户只负责：

- 提供或确认 NAS 真实目录；
- 提供模型 API Key、夸克账号凭据、代理凭据等必要秘密；
- 完成扫码、验证码、夸克登录或教父登录等不能可靠自动化的身份验证；
- 确认会修改系统或产生真实下载的操作。

其余配置生成、容器部署、网络接入、初始化、验证和回滚由部署器完成。

### 2.4 首版非目标

- OpenClaw Telegram Bot、飞书或其他消息渠道；
- 对所有 NAS 平台提供正式兼容承诺；
- 自动绕过验证码、登录风控或网站安全挑战；
- 自动选择影视资源版本；
- 在完整验收中自动把测试文件写入正式媒体库；
- 为每个模型厂商编写独立适配器；
- 自动删除用户原有容器、真实下载文件或正式媒体库内容。

## 3. 核心原则

1. **唯一配置源**：`deploy/config.yaml` 是全部非敏感配置的唯一真实来源。
2. **秘密隔离**：Cookie、Token、API Key、登录态和代理凭据放入独立 secret 文件。
3. **计划先行**：发现、计划和执行分离；没有有效计划及明确确认不得修改环境。
4. **确定性优先**：默认使用经过验证并锁定的镜像 tag 和 digest。
5. **幂等执行**：相同配置重复执行不产生不必要变更。
6. **失败可恢复**：每个写操作必须记录备份和逆操作。
7. **机器可读**：部署器命令只向标准输出写一个 JSON 文档。
8. **安全不可绕过**：正式媒体库保护、secret 权限和 allowlist 等安全阻断不能使用强制参数绕过。
9. **真实验收**：区分容器存活、组件可用和业务链闭环。
10. **业务与部署分离**：部署代码位于 `deploy/`，不把 NAS 探测和 Compose 管理混入 `mediactl` 核心流程。

## 4. 技术方案

### 4.1 宿主机 Python 部署器

部署器以 Python 3 运行在 NAS 宿主机，直接访问 Docker CLI、Docker Compose、宿主机文件系统、已有 OpenClaw 配置、容器网络、挂载和实际 UID/GID。

选择该方案的原因：

- 最适合绿联 UGOS 和标准 Linux；
- 能可靠识别真实宿主机路径和已有容器；
- 便于处理权限、ACL、备份和回滚；
- 核心逻辑可以单元测试；
- 后续可以把同一套 Python 代码封装为 Installer 容器，不维护第二套部署逻辑。

Shell 只允许用于极薄的启动包装。YAML、JSON、计划签名、配置差异、秘密脱敏、事务回滚和版本适配由 Python 实现。

### 4.2 建议仓库结构

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

## 5. 命令接口与输出契约

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

`PLAN_ID` 和 `DEPLOYMENT_ID` 是部署器运行时生成的不可猜测标识符。

每个命令只向标准输出写一个 JSON 文档。诊断日志写入标准错误或报告文件，并经过脱敏。

统一字段：

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

## 6. 配置模型

### 6.1 `config.yaml`

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

`base_url` 和 `model` 是用户配置值，不是部署器内置厂商逻辑。部署器只验证 OpenAI-Compatible 接口是否可以完成最小模型请求。

### 6.2 `auto` 解析规则

1. 查找匹配的现有容器、路径或配置。
2. 检查镜像、端口、挂载、网络和名称。
3. 只有唯一且可信的结果才自动选择。
4. 多个候选、冲突或不完整结果必须停止。
5. 候选写入计划，由用户明确选择。
6. 不得静默覆盖现有服务。

### 6.3 交互式向导

`init` 提供交互式向导，但只生成或修改 `config.yaml` 和 secrets 模板，不直接部署。Agent 也可以直接生成相同配置文件。两种入口必须进入同一套 schema 验证和执行流程。

## 7. 秘密管理

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

- secrets 目录权限为 `0700`；
- secret 文件权限为 `0600`；
- storage state JSON 必须可解析；
- 容器通过只读挂载或启动时读取；
- secrets 不进入 Git、备份包、计划差异或验收报告；
- 异常信息必须做值替换和字段级脱敏；
- 备份只记录 secret 是否存在及其哈希，不保存真实内容。

首版不依赖 Docker Swarm secrets 或特定 NAS 密钥管理器。

## 8. 版本锁定

`deploy/versions.yaml` 必须包含以下必填字段：

- `schema_version`；
- 每个组件的完整镜像名；
- 明确 tag；
- 完整 `sha256` digest；
- 适用 CPU 架构；
- 验证日期；
- 与其他组件的兼容组合；
- 需要的配置 adapter 名称。

仓库不得提交包含 `latest`、通配 tag、空 digest 或示例镜像名的正式版本锁文件。

规则：

- 默认使用经过维护者 `safe` 和 `full` 验收的 tag 与 digest；
- `config.yaml` 可以覆盖版本，但计划必须标记 `unverified_version_override`；
- `versions check` 只检查更新，不自动修改版本锁；
- 配置结构变化通过新 adapter 处理；
- 旧 adapter 不承担未验证版本的兼容承诺。

## 9. 执行流程

```text
init
  → discover
  → plan
  → 用户确认
  → apply
  → verify safe
  → 可选 verify full
```

### 9.1 `discover`

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

### 9.2 `plan`

- 校验 schema；
- 解析 `auto`；
- 计算 Compose、配置、网络、挂载和权限差异；
- 生成备份范围和回滚步骤；
- 生成唯一 `planId`；
- 对配置、secret 哈希摘要、发现结果和目标文件状态计算计划指纹；
- 计划有效期固定为 30 分钟；
- 标记所有副作用和用户确认点。

### 9.3 `apply`

必须带 `--plan-id` 和 `--confirmed`。执行前重新校验：

- 计划未超过 30 分钟；
- 配置指纹未变化；
- secret 文件哈希未变化；
- 关键容器、配置文件和挂载未发生漂移；
- 备份创建成功。

任一条件不满足时，计划失效，必须重新运行 `discover` 和 `plan`。

### 9.4 幂等规则

- 相同目录不重复创建；
- 配置未变化不重启服务；
- 容器配置一致不重建；
- Skill 已是目标版本不重复安装；
- 已接入网络不重复操作；
- 检测到用户手工修改时显示差异并重新计划；
- 无法安全合并时停止，不强制覆盖。

## 10. 组件初始化

### 10.1 OpenClaw：`existing-openclaw`

部署器必须识别 OpenClaw 容器、镜像、workspace 主机路径和容器路径、配置文件、Compose 项目、网络、Skill 配置结构及 exec allowlist。

增量流程：

1. 备份 Compose、OpenClaw 配置和现有 Skill。
2. 将 OpenClaw 接入共享网络。
3. 添加下载目录和媒体库挂载。
4. 安装或更新 `resource-download-agent`。
5. 写入 Skill 环境变量。
6. 只允许固定绝对路径的 `mediactl`。
7. 按版本 adapter 重载或重启。
8. 验证 Skill 被发现且能执行固定命令。

无法自动识别时：

- 用户可以通过 `config.yaml` 明确给出容器名和路径时，返回 `manual_action_required`；
- 已知版本不受支持或配置无法安全修改时，返回 `failed`；
- 不得通过猜测写入。

### 10.2 OpenClaw：`full-stack`

完整 Compose 必须包含 OpenClaw、workspace 与配置持久化、OpenAI-Compatible 模型配置、Web／本地对话入口、Skill 目录、下载及媒体库挂载、共享网络、固定 `mediactl` allowlist 和健康检查。

首版不配置 Telegram Bot、飞书或其他消息渠道。

### 10.3 QAS

按以下顺序初始化。

#### 配置文件 adapter

根据锁定版本写入 WebUI 账号、API Token、夸克 Cookie、aria2 RPC 地址、RPC Secret、下载目录和插件启用状态。写入前备份，写入后重启并回读验证。

#### 稳定 API

当前锁定版本提供稳定配置 API 时，读取当前配置、生成脱敏差异、写入配置、重新读取并逐字段核验，然后执行 QAS 到 aria2 的链路检查。

#### 浏览器自动化兜底

配置文件和 API 均不适配时，启动临时浏览器并打开 NAS 本地 QAS WebUI。用户只完成扫码、验证码或登录，其余配置由自动化填写。完成后关闭浏览器并重新执行 QAS 验收。

需要用户操作时返回：

```json
{
  "ok": false,
  "status": "manual_action_required",
  "nextAction": "complete_qas_login"
}
```

QAS 容器存活但 Cookie、Token 或 aria2 插件未配置时，不能标记为 `ready`。

### 10.4 PanSou 与代理

代理模式：

- `none`：当前网络直接访问 Telegram；
- `existing`：复用用户提供的 SOCKS5 或 HTTP 代理；
- `managed`：启用仓库内置 proxy Compose profile，用户仍需提供合法节点或订阅配置。

部署器按代理类型设置 PanSou 支持的代理环境变量，不得将代理凭据写入报告。

PanSou 验收必须区分：

1. 容器健康；
2. API 可达；
3. 频道配置生效；
4. Telegram 数据源实际可用；
5. 代理失败能够明确定位。

PanSou 或代理失败时核心系统可以为 `degraded`，但启用该组件时不能报告整体 `ready`。

### 10.5 Jiaofu Runner

教父 Playwright 环境从 OpenClaw／`mediactl` 进程分离为独立内部服务：

```text
Jiaofu Runner
├── Python
├── Playwright
├── Chromium
├── storage state
└── internal HTTP API
```

内部接口：

```text
GET  /health
GET  /session/status
POST /session/login/start
POST /search
```

`POST /search` 只接收查询词和最大候选数，只返回标题、规范化夸克分享链接及安全状态，不返回 Cookie 或原始页面内容。

`mediactl` 增加 `JIAOFU_BASE_URL`，通过内部 HTTP API 调用 Runner。为兼容现有部署，一个过渡版本可以保留本地 `JIAOFU_STORAGE_STATE` 模式；新部署默认使用 Runner。

登录流程：

- 检查 Chromium 可启动；
- storage state 不存在或过期时返回 `manual_action_required`；
- 用户完成一次登录；
- Runner 保存 storage state；
- 执行测试查询并验证合法夸克链接；
- 登录过期返回 `nextAction=refresh_jiaofu_login`。

Jiaofu 是可选组件。失败时系统可以为 `degraded`，不阻断直接分享链接、QAS 或 PanSou。

### 10.6 aria2 与权限

部署器必须读取容器实际运行身份、目录 owner、group、mode 和平台 ACL 能力，不假定 aria2 一定使用 `nobody:nogroup`。

权限策略顺序：

1. UID/GID 对齐；
2. 共享用户组；
3. POSIX ACL；
4. 仅对托管下载根和 `.incoming` 使用宽松权限；
5. 永不递归修改正式媒体库。

计划必须列出权限差异、理由和影响。未经确认不得修改。

OpenClaw 和 aria2 必须挂载同一宿主机下载目录；aria2 内路径固定为 `/nas/downloads`，OpenClaw 内路径与生成的 `routing.json` 一致。

## 11. 分层验收

### L0：静态配置

校验 YAML、JSON、Compose、schema、secret 权限、镜像版本和架构、端口冲突、主机路径、挂载真实性及正式媒体库保护关系。

### L1：容器健康

检查所有已启用组件的健康检查，不以单纯 `docker ps` 作为通过条件。

### L2：内部网络

验证 OpenClaw 到 QAS、PanSou、aria2 RPC、Jiaofu Runner 的连接，以及 PanSou 到 Telegram／代理和 QAS 到 aria2 的连接。

### L3：组件功能

验证 QAS Token、QAS Cookie、QAS aria2 插件、PanSou Telegram 来源、Jiaofu 合法链接、aria2 共享目录写入、OpenClaw Skill 发现和 `mediactl check-ready`。

### L4：安全验收

验证正式媒体库不能作为下载目标、protected roots 生效、下载与整理均需确认、OpenClaw 只能调用固定 `mediactl`、日志不泄露 secret、旧计划不能执行、受保护内容删除请求被拒绝。

### L5：`safe`

默认执行，不产生真实转存：

```text
OpenClaw 对话
  → mediactl
  → 本地检索
  → 远端只读搜索或导入合法测试链接
  → 候选预览
  → 目录树
  → 下载计划
  → 停止
```

### L6：`full`

必须同时满足以下三个条件：

1. `verification.allow_real_download` 为 `true`；
2. `full_test_share_url` secret 存在并通过合法夸克分享链接格式校验；
3. 命令带 `--confirmed`。

流程：

```text
导入测试链接
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

默认禁止执行 `organize execute`，测试文件不进入正式媒体库。

## 12. 状态与错误模型

最终状态只能是：

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

## 13. 备份与回滚

事务流程：

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

每个写操作记录逆操作。自动回滚范围包括 OpenClaw 配置与 Compose、QAS 配置、Skill 旧版本、本次新建且未承载用户数据的容器、本次新增网络连接、本次修改的托管目录权限、`routing.json` 和本项目状态数据库。

默认不自动删除用户原有容器、真实下载文件、正式媒体库内容、用户已有网络和数据卷、用户提供的代理配置和凭据。

已经产生真实下载时，回滚只恢复配置并报告遗留文件位置。

## 14. 报告文件

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

报告必须包含组件状态、复用或新建决策、路径映射、网络关系、脱敏配置状态、验收层级与结果、备份位置、回滚命令和明确 `nextAction`。

## 15. 测试策略

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

### 单元测试

覆盖 schema、默认值、`auto` 决策、计划指纹和失效、脱敏、权限决策、版本适配、备份、逆操作和状态模型。

### Compose 集成测试

使用临时目录和模拟服务验证模板渲染、容器网络、健康检查、路径映射、配置挂载、重复 apply 的幂等性和中途失败后的回滚。

### 端到端测试

在受控环境验证完整组件组合。CI 不接触真实 Cookie、夸克账号、代理节点或教父登录态。真实 `full` 验收由维护者手动触发并使用专用测试账户和合法测试资源。

## 16. 文档重构

README 只保留新手入口和两种部署模式，详细文档拆分为：

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

README 对部署能力的表述为：

> Agent 读取配置并调用仓库内置部署器完成安装、初始化、验收和回滚；只有扫码、验证码及危险操作确认需要用户参与。

## 17. 分阶段交付

### 第一阶段：`existing-openclaw`

- 配置 schema、向导和 secret 管理；
- UGOS／Linux 发现；
- 计划、备份、回滚和脱敏；
- 依赖 Compose 和版本锁；
- 已有 OpenClaw adapter；
- QAS、PanSou、aria2 初始化；
- `safe` 验收；
- 兼容现有本地 Playwright 模式。

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

## 18. 验收标准

### `existing-openclaw`

必须同时满足：

1. 新手可以通过向导生成有效配置和 secrets 模板；
2. Agent 可以只根据 `config.yaml` 调用部署器完成计划和执行；
3. 不要求 Agent 临场编辑 Compose、OpenClaw JSON、QAS 配置或 `routing.json`；
4. 重复执行相同配置不产生不必要重启或容器重建；
5. QAS 容器存活但 Cookie 或 aria2 插件未配置时验收失败；
6. PanSou 启用但 Telegram 或代理不可用时状态为 `degraded`；
7. aria2 写入目录映射通过真实探针验证；
8. OpenClaw 能在对话中加载 Skill 并调用固定 `mediactl`；
9. `safe` 验收不创建真实下载；
10. `full` 验收必须满足三项开启条件，并停在整理计划；
11. 报告不包含真实 secret；
12. 核心失败可以恢复到执行前配置；
13. 正式媒体库内容在部署、验收和回滚中不会被删除或覆盖。

### `full-stack`

除上述标准外还必须满足：

1. 空白 UGOS 或标准 Linux Docker 主机可以部署 OpenClaw 和全部核心依赖；
2. OpenAI-Compatible 模型连接成功；
3. Web／本地对话入口可用；
4. OpenClaw 可以加载本项目 Skill；
5. `safe` 和维护者 `full` 验收通过；
6. 用户不需要手工编辑配置文件，只需填写配置、secret 并完成必要登录。

## 19. 主要风险与缓解

- **QAS 配置变化**：锁定版本、使用 adapter、写后回读；不能识别的版本不自动修改。
- **OpenClaw 配置差异**：版本 adapter、备份和失败停止；首版记录经过验证的 OpenClaw 版本。
- **登录风控**：不尝试绕过，返回 `manual_action_required`。
- **UGOS 权限和 ACL**：平台 adapter、真实 UID/GID 探测和受限权限变更，正式媒体库永不递归 chmod。
- **代理和 Telegram 不稳定**：PanSou 可降级，独立报告连通性，不把空结果误报为成功。
- **浏览器镜像架构支持**：分别验证 amd64 和 arm64；不支持时关闭可选 Jiaofu 并报告 `degraded`。

## 20. 结论

部署能力必须从 README 中的建议步骤升级为仓库内置的确定性部署程序。

Agent 最终只负责：

1. 收集用户必须提供的信息；
2. 调用 `init`、`discover`、`plan`、`apply` 和 `verify`；
3. 展示变更并请求确认；
4. 根据结构化状态指导用户完成一次登录或处理明确错误。

目标体验是：新手填写配置和 secrets、完成必要登录、确认部署和真实下载，其余安装、初始化、验证及回滚由部署器闭环完成。