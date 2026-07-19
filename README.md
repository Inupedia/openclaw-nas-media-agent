# OpenClaw NAS Media Agent

面向 NAS 的影视资源 Skill：让 OpenClaw 先查本地媒体库，再搜索远端候选，由用户选择版本，下载到独立暂存区，校验后才整理进媒体库。

已在 UGREEN（绿联云）NAS 的 Docker 环境中完成部署与验收。其他支持 Docker、Python 3 和目录挂载的 NAS 也可以适配。

## 第一步：直接把项目交给智能体

把下面这段话发给运行在 NAS 上、具有 Docker 管理权限的智能体。推荐先让智能体只检查环境并给出安装计划，确认后再执行。

```text
请把这个 GitHub 项目安装到我的 NAS：
https://github.com/Inupedia/openclaw-nas-media-agent

请先阅读 README.md、SKILL.md、config/routing.json 和测试，不要直接照搬示例路径。

要求：
1. 先检查 NAS 类型、OpenClaw 的 workspace、Docker Compose 项目、QAS 和 aria2 是否已经存在。
2. 询问并确认下载暂存目录、正式影视库和临时影视库的真实路径。
3. 用 git clone 安装到 OpenClaw workspace 的 skills/resource-download-agent。
4. 给 OpenClaw、QAS 和 aria2 挂载同一个下载目录；aria2 容器内必须能以 /nas/downloads 访问该目录。
5. 复制 `.env.example` 为环境变量配置，填写 QAS_BASE_URL、QAS_TOKEN、PANSOU_BASE_URL、PANSOU_MAX_CANDIDATES、ARIA2_RPC_URL、ARIA2_RPC_SECRET 和 RESOURCE_AGENT_STATE_DB；绿联云可选配置 VIDEOMGR_*。绝对不要在回复、日志或提交中输出真实凭据或真实内网地址。
6. OpenClaw 的 exec 使用 allowlist，ask 关闭，只允许执行本项目 bin/mediactl 的固定绝对路径；不要开放任意 shell、Python、curl 或 sudo。
7. /volume2/影视 和 /volume3/临时影视 是永久保护库：不得删除、覆盖、清理或把已有内容移出。我的路径不同时，请修改 routing.json 和保护根目录后再部署。
8. 先运行完整测试和 mediactl check-ready，再用“只预览、不下载”的搜索做验收。
9. 任何会修改 Docker 配置、移动文件或启动真实下载的操作，先向我展示计划和影响；不要擅自执行。
10. 完成后报告安装路径、挂载、环境检查、测试结果、回滚备份和我可以直接说的示例命令。
```

智能体安装是本项目的首选方式，因为不同 NAS 的卷名、Docker 管理方式、OpenClaw 镜像和现有下载服务通常不同。不要在没有审计路径和权限的情况下直接复制别人的 Compose 配置。

## 手动 Git 安装

下面以绿联云常见的 `/volume4/openclaw` 作为 OpenClaw workspace 为例：

```bash
cd /volume4/openclaw/skills
git clone https://github.com/Inupedia/openclaw-nas-media-agent.git resource-download-agent
chmod 0755 resource-download-agent/bin/mediactl
mkdir -p /volume4/openclaw/data/resource-download-agent

# 教父.com 发现依赖 Playwright（可选）。在技能目录创建持久 venv：
cd /volume4/openclaw/skills/resource-download-agent
python3 -m venv .venv
.venv/bin/pip install "playwright>=1.40"
.venv/bin/playwright install chromium
.venv/bin/playwright install-deps chromium   # 容器内需要系统库时
# 并把登录态放到 data/jiaofu_storage_state.json，配置 JIAOFU_STORAGE_STATE
```

如果容器内的 OpenClaw workspace 是 `/root/.openclaw/workspace`，最终命令路径应为：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl
```

更新：

```bash
cd /volume4/openclaw/skills/resource-download-agent
git pull --ff-only
```

更新前建议备份 Skill 目录和状态数据库；更新后重新运行测试与 `check-ready`。

## 它解决什么问题

传统“搜到就下”的自动化很容易重复下载、选错大体积版本，甚至直接写进正式媒体库。本项目把流程拆开：

1. 先查询 NAS。
2. 本地已有时停止普通远端搜索。
3. 追更时只计算缺失集数。
4. 用 QAS + PanSou 全面发现候选，再由 QAS 深度检查实际文件。
5. 把不同规格全部列给用户选择。
6. 只把选中的内容下载到暂存区。
7. 下载完成后校验。
8. 单独确认后整理进媒体库。

支持的媒体类型：

- `movie`：电影
- `drama`：电视剧，推荐的新名称
- `tv`：电视剧旧任务兼容名称
- `anime`：动画、动漫
- `documentary`：纪录片
- `show`：综艺
- `other`：其他影视内容

候选规格会尽量展示分辨率、HDR/Dolby Vision、视频编码、音频格式、字幕类型、总大小、文件数和剧集范围。中英双语字幕会在同等候选中优先排列，但智能体不得自动替用户选择。

## 当前能力

- NAS 本地资源优先查询。
- 普通搜索、本地存在即停止。
- 电视剧和动画的缺集计算与增量候选。
- 远端候选深度预览和不同规格分组。
- 用户指定候选后生成不可变、限时、单次使用的下载计划。
- 查看、暂停、继续和取消本项目创建的 aria2 任务。
- 取消任务默认保留已经下载的数据。
- 下载完成后的文件、大小、临时文件和视频可读性校验。
- 经过单独确认的媒体整理。
- JSON 输出白名单和敏感字段清理。

当前版本不包含通用文件管理、自动删除媒体、自动转码、定时追更或定时推荐。需要这些功能时，应在独立安全设计和测试完成后再扩展。

## 依赖

### OpenClaw

OpenClaw 负责识别用户意图、读取 `SKILL.md` 并调用固定的 `mediactl`。建议：

- `tools.exec.security = allowlist`
- `tools.exec.ask = off`
- allowlist 中只加入 `mediactl` 的固定绝对路径
- 禁止通用 shell、任意 Python、`curl`、代码执行和 elevated 权限

具体配置命令会随 OpenClaw 版本变化，安装智能体应先读取当前版本配置帮助再修改。

### QAS

本项目通过 QAS HTTP API 搜索、预览和执行网盘任务。已验证的部署使用 `cp0204/quark-auto-save`，但仓库不保存 QAS Cookie、Token 或分享链接。

### PanSou

PanSou 只用于补充发现夸克候选。每个候选仍由 QAS 预览、过滤和执行；PanSou 不接触 Cookie，不直接转存或下载。相同分享会去重，默认最多接纳 50 个 PanSou 候选，配置上限为 100。

### aria2

aria2 负责真实下载。它必须启用 JSON-RPC，并与 OpenClaw 看到同一份下载目录。

推荐的路径对应关系：

| NAS 主机路径 | OpenClaw 容器 | aria2 容器 |
|---|---|---|
| `/volume2/downloads` | `/volume2/downloads` | `/nas/downloads` |
| `/volume2/影视` | `/volume2/影视` | 不需要写入权限 |
| `/volume3/临时影视` | `/volume3/临时影视` | 不需要写入权限 |
| `/volume4/openclaw` | `/root/.openclaw/workspace` | 不需要挂载 |

QAS 和 aria2 都不得把正式媒体库作为直接下载目标。

### Python

运行时只使用 Python 3 标准库。可选的 `ffprobe` 用于增强视频可读性检查；未安装时仍会执行其他校验。

## 环境变量

复制 `.env.example` 为 `.env`（或写入 OpenClaw 容器 `environment`），按本机服务填写。仓库只保留占位值：

```bash
cp .env.example .env
```

```yaml
environment:
  QAS_BASE_URL: "http://<qas-host>:<qas-port>"
  QAS_TOKEN: "<qas-token>"
  PANSOU_BASE_URL: "http://<pansou-host>:<pansou-port>"
  PANSOU_MAX_CANDIDATES: "50"
  ARIA2_RPC_URL: "http://<aria2-host>:<aria2-port>/jsonrpc"
  ARIA2_RPC_SECRET: "<aria2-rpc-secret>"
  RESOURCE_AGENT_STATE_DB: "/root/.openclaw/workspace/data/resource-download-agent/state.db"
  # Optional — UGREEN 影视中心本地库（Docker 内需能访问 NAS 上的 API / Redis）
  # VIDEOMGR_ENABLED: "auto"
  # VIDEOMGR_BASE_URL: "http://<nas-gateway-host>:9999"
  # VIDEOMGR_REDIS_HOST: "<nas-redis-host>"
  # VIDEOMGR_PREFER_USER: "<ugreen-username>"
```

不要把 `.env`、Cookie、Token、RPC Secret、Authorization Header 或真实内网地址提交到 Git。

本地库查询顺序：先扫 `routing.json` 下的目录与散落视频文件（支持中英混名），未命中时再尝试影视中心搜索 API（需有效 UGOS 会话）。

## 绿联云 Docker 挂载示例

这只是路径结构示例，安装前必须按自己的存储卷调整：

```yaml
services:
  gateway:
    volumes:
      - /volume4/openclaw:/root/.openclaw/workspace
      - /volume2/downloads:/volume2/downloads
      - /volume2/影视:/volume2/影视
      - /volume3/临时影视:/volume3/临时影视
      # Cross-disk movie organize staging; must share the volume3 filesystem
      - /volume3/.openclaw-organizing:/volume3/.openclaw-organizing
```

aria2 至少需要：

```yaml
services:
  aria2:
    volumes:
      - /volume2/downloads:/nas/downloads
```

`routing.json` 的 downloads 路径映射：

| 角色 | 典型路径 | 说明 |
| --- | --- | --- |
| `host_root` / `agent_root` | `/volume2/downloads` | OpenClaw 容器内看到的下载根 |
| `aria2_root` | `/nas/downloads` | aria2 容器内同一挂载点 |

正式库（`/volume2/影视`、`/volume3/临时影视`）必须预先挂载存在；程序不会自动创建这些目录，避免挂载失效时落到容器本地假目录。

路径不是这些默认值时，需要同时修改：

- `config/routing.json`
- OpenClaw 的 Docker volumes（含 organizing root）
- aria2 的 `/nas/downloads` 挂载
- README 或本地运维记录中的路径说明

不要只改 `routing.json` 而忘记保护根目录与 organizing 挂载。

## 默认存储流程

```text
用户请求
  ↓
NAS 本地查询
  ├─ 普通搜索且本地已有 → 报告本地结果并停止
  └─ 本地没有 / 明确要求更新
        ↓
QAS + PanSou 聚合发现候选
        ↓
QAS 预览、验证并提取规格
        ↓
按画质、编码、字幕、大小和集数列出全部有效规格
        ↓
用户选择 candidateId
        ↓
生成下载计划 → 必要时再次确认
        ↓
/volume2/downloads/.incoming/<task-id>
        ↓
完成后校验 → .ready 或 .quarantine
        ↓
生成独立整理计划
        ↓
用户明确确认
        ↓
/volume2/影视 或 /volume3/临时影视
```

默认路由位于 `config/routing.json`：

- 电影：`/volume3/临时影视/Movie`
- 电视剧：`/volume2/影视/Drama`
- 动画：`/volume2/影视/Anime`
- 纪录片：`/volume2/影视/Documentary`
- 综艺：`/volume2/影视/Shows`
- 其他：`/volume2/影视/Others`

## 用户可以直接怎么说

```text
搜索《凡人修仙传》动画资源，先预览，不要下载。
```

```text
检查《凡人修仙传》有没有新集，只列缺少的集数和可选版本。
```

```text
列出当前下载任务和进度。
```

```text
暂停任务 TASK_ID。
```

```text
校验任务 TASK_ID，先不要整理。
```

对于只包含“搜索、看看、预览、推荐”的请求，智能体不得创建下载计划。

## `mediactl` 命令

安装和配置后先检查：

```bash
bin/mediactl check-ready
```

搜索与更新：

```bash
bin/mediactl search "凡人修仙传" --media-type anime
bin/mediactl search "凡人修仙传" --media-type anime --update
bin/mediactl search "电视剧名" --media-type drama
```

预览、计划和执行：

```bash
bin/mediactl preview CANDIDATE_ID
bin/mediactl plan download CANDIDATE_ID
bin/mediactl execute PLAN_ID
bin/mediactl execute PLAN_ID --confirmed
```

下载管理：

```bash
bin/mediactl downloads list
bin/mediactl downloads show TASK_ID
bin/mediactl downloads pause TASK_ID
bin/mediactl downloads resume TASK_ID
bin/mediactl downloads cancel TASK_ID
bin/mediactl downloads validate TASK_ID
```

整理：

```bash
bin/mediactl organize plan TASK_ID
bin/mediactl organize execute PLAN_ID --confirmed
```

所有命令只输出一个 JSON 文档。智能体应优先读取 `ok`、`terminal`、`nextAction`、`data` 和 `error`，不要解析 Markdown。

候选中的 `discoverySources` 只会包含 `qas` 和 `pansou`，用于说明发现来源；不会返回底层分享链接或服务地址。PanSou 暂时不可用时，搜索会给出安全警告并继续使用 QAS 结果。

## 安全边界

### 永久保护的正式媒体库

`/volume2/影视` 和 `/volume3/临时影视` 内已有内容：

- 不得删除。
- 不得覆盖。
- 不得清理。
- 不得移动出去。
- 即使用户明确要求，OpenClaw 也必须拒绝。

整理操作只允许把经过校验的新内容从 `/volume2/downloads` 加入媒体库；目标已存在时停止。

### 下载区

- 新任务只能进入 `/volume2/downloads/.incoming/<task-id>`。
- 校验成功后才进入 `.ready`。
- 异常内容进入 `.quarantine`。
- 取消 aria2 任务默认保留文件。
- 当前 CLI 不提供任意路径删除能力。

### 执行权限

OpenClaw 只需要执行：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl
```

不要为了排错开放 `rm`、`find`、`du`、`python`、`curl`、任意 shell 或 `sudo -i`。底层服务故障时，先报告安全错误，再由管理员处理。

## 测试

在仓库根目录运行：

```bash
python3 -m unittest discover -s tests -v
```

Windows 无法创建符号链接时，相关路径逃逸测试会跳过；Linux/NAS 环境应允许这些测试运行。

部署后至少验收：

1. `mediactl check-ready` 返回 `nextAction: ready`。
2. 普通搜索本地已有作品时，不调用远端搜索。
3. 只预览请求不会创建 `.incoming` 任务。
4. 多个有效候选会分别显示规格，不自动选择。
5. 更新只返回缺集。
6. 非 allowlist 命令直接拒绝，不出现 `/approve`。
7. 受保护媒体库删除请求直接拒绝。

## 常见问题

### `check-ready` 报路径不可写

确认 OpenClaw 容器内存在 `/volume2/downloads/.incoming`，运行用户对下载区有写权限，并且 Compose 挂载没有写错卷。创建目录时请保证 aria2 的 `nobody` 用户可写（建议 `.incoming` / `.ready` / `.quarantine` 为 `777`，或 chown 给 aria2 运行用户）。

### aria2 可以连接但任务路径不对

检查 aria2 是否把同一个主机下载目录挂载为 `/nas/downloads`。本项目提交给 aria2 的保存路径以 `/nas/downloads/.incoming/...` 开头。

### aria2 任务立刻 error 18（Download aborted）且 `.incoming` 为空

`aria2-pro` 里的 `aria2c` 通常以 `nobody` 运行。若主机上 `/volume2/downloads/.incoming` 是 `770 root:root`，nobody 无法建目录，任务会马上 abort，磁盘上看不到文件。

处理：

```bash
chmod 777 /volume2/downloads/.incoming /volume2/downloads/.ready /volume2/downloads/.quarantine
```

然后重新执行下载（或让 QAS 再推一次）。QAS 日志里若已有 `📥 Aria2下载`，说明转存已成功，问题在本机写权限，不在夸克。

### 夸克网盘里找不到转存

转存账号以 QAS Cookie 为准（当前常见昵称在 QAS 日志的「转存账号」一行）。云盘路径是 ASCII 任务目录，例如：

`/OpenClaw/Others/rd-<taskid>`

不要按中文剧名在「最近」或「影视」根目录找；早期中文路径会因 `illegal text` 建目录失败。

### 搜索结果很少

远端候选会经过实际分享读取、空目录、失效资源、纯压缩包和无视频文件过滤。结果少不一定是搜索失败；查看 JSON 中的拒绝计数。

### 本地已有但仍想找其他版本

普通搜索会优先停止，避免重复。当前版本没有“替换已有版本”自动流程；请先人工评估，不要绕过正式库保护。

### 为什么不自动选择 4K

4K、HDR 或高码率版本可能非常大。项目只做辅助排序，保留所有有效规格给用户选择；中英双语字幕是优选项之一，不是自动下载条件。

## 兼容性

- 已验证：UGREEN NAS、Docker、OpenClaw、QAS、aria2。
- 预期可适配：群晖、威联通、TrueNAS、Unraid 和普通 Linux Docker 主机。
- 未验证的平台需要自行检查路径、权限、容器网络和 Python 版本。

## License

本项目采用 [MIT License](LICENSE)。你可以使用、复制、修改和分发，但需保留版权与许可声明。软件按原样提供，不附带任何担保。
