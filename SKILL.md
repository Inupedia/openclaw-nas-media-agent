---
name: resource-download-agent
description: Use when 搜索/预览 NAS 影视动画、追更补集、下载监控暂停、校验整理入库；删除受保护库内容时拒绝。
version: 0.4.0
homepage: https://github.com/Inupedia/openclaw-nas-media-agent
metadata:
  openclaw:
    os:
      - linux
    primaryEnv: QAS_TOKEN
    requires:
      bins:
        - python3
      env:
        - QAS_BASE_URL
        - QAS_TOKEN
    envVars:
      - name: QAS_BASE_URL
        required: true
        description: QAS API endpoint
      - name: QAS_TOKEN
        required: true
        description: QAS API credential
      - name: PANSOU_BASE_URL
        required: false
        description: Optional PanSou discovery endpoint
      - name: PANSOU_MAX_CANDIDATES
        required: false
        description: PanSou unique candidate limit (default 50, max 100)
      - name: JIAOFU_STORAGE_STATE
        required: false
        description: Playwright storage state for jiaofu.com
      - name: JIAOFU_MAX_CANDIDATES
        required: false
        description: Jiaofu candidate limit (default 20, max 50)
      - name: ARIA2_RPC_URL
        required: false
        description: Required at runtime for check-ready and downloads
      - name: ARIA2_RPC_SECRET
        required: false
        description: aria2 RPC credential
      - name: ARIA2_PROBE_URL
        required: false
        description: HTTP(S) URL for check-ready write probe; set skip to disable
      - name: RESOURCE_AGENT_STATE_DB
        required: false
        description: Agent state DB path; defaults under skill data/
      - name: QUARK_RECOVERY_ENABLED
        required: false
        description: Enable confirmed Quark Cookie re-push (default false)
      - name: QUARK_RECOVERY_MAX_ATTEMPTS
        required: false
        description: Max Quark recovery attempts per task (default 2)
      - name: QUARK_RECOVERY_COOLDOWN_SECONDS
        required: false
        description: Cooldown after failed recovery attempt (default 300)
      - name: VIDEOMGR_ENABLED
        required: false
        description: Enable UGREEN Theater local search auto/1/0
      - name: VIDEOMGR_BASE_URL
        required: false
        description: Theater HTTP base
      - name: VIDEOMGR_SOCK
        required: false
        description: Unix socket to video_serv when mounted
      - name: VIDEOMGR_TOKEN
        required: false
        description: UGOS session token; prefer Redis discovery
      - name: VIDEOMGR_REDIS_HOST
        required: false
        description: Redis host for UGTOKEN discovery
      - name: VIDEOMGR_REDIS_PORT
        required: false
        description: Redis port for UGTOKEN discovery
      - name: VIDEOMGR_PREFER_USER
        required: false
        description: Prefer this UGOS username for UGTOKEN
---

# Resource Download Agent

只处理 NAS 影视库查询、夸克候选预览、精确补集下载、校验与整理。不处理压缩、转码或释放空间。

唯一可执行入口：

```text
{baseDir}/bin/mediactl
```

命令必须是上述路径开头的**单一** `mediactl ...` 调用。禁止 `chmod`、`ls`、`bash -lc`、管道、`&&` 或其他包装。失败时只报告错误，不得绕过。

详细命令与示例见 `{baseDir}/references/commands.md`、`{baseDir}/references/examples.md`。

## 不变量

1. 所有系统操作只通过 `{baseDir}/bin/mediactl`。
2. `search` / `preview` / `tree` / `library` / `import-url` 不产生转存、下载或媒体文件写入；允许写入本地候选状态库。不得为“看看内容”而 `execute`。
3. 任何下载必须经过：`candidate` → `tree` → 用户确认 `node` → `plan download` → 用户确认 → `execute ... --confirmed`。
4. 任何入库必须经过：`complete` → `validate` → `organize plan` → 用户确认 → `organize execute --confirmed`。
5. JSON `terminal: true` 时立即停止；只按 `status` / `nextAction` / `recovery` 分支，不凭文字猜测。
6. `/volume2/影视` 与 `/volume3/临时影视` 永不删除、覆盖或移出；即使用户要求也拒绝。整条回复只能是：`拒绝：OpenClaw 不会删除或协助删除受保护媒体库中的内容。`
7. `mediactl` 失败时不得改用其他工具、脚本、HTTP、或 `web_search` / `web_fetch` 找片。
8. 不得输出 Cookie、Token、RPC Secret、Header、环境变量或原始底层响应。
9. `downloads list/show` 只读同步状态；禁止因查询而重推下载。error 16 仅当 `nextAction=confirm_recover` 时，经用户确认后走 `downloads recover plan` → `downloads recover execute ... --confirmed`。

## 状态机

| 当前结果 | Agent 动作 |
| --- | --- |
| `stop_local_exists` | 报告本地结果并停止 |
| `already_up_to_date` | 报告无缺集并停止 |
| `choose_candidate` / 多候选 | 展示全部候选，等待用户选择；不得自动挑“最好” |
| `choose_tree_nodes` | 读 `tree`，解释并建议 `nodeId`，等待确认 |
| `review_plan` / 已生成 plan | 展示 mediaType、节点、`finalPath`、副作用；等待确认后 `execute PLAN_ID --confirmed` |
| `monitor_download` / 刚 execute | 立即 `downloads show` 一次；本轮最多再查 2 次 |
| `complete` | `downloads validate` |
| `ready_to_organize` | `organize plan`，再确认后 `organize execute --confirmed` |
| `quarantine_download` / `quarantined` | 报告问题并停止；人工修好后可再 `validate` |
| `partial_failed` / `error` | 报告并停止；不自动重试或重新 `execute` |
| `confirm_recover` | 展示 `recovery`；确认后 `downloads recover plan` → `downloads recover execute PLAN_ID --confirmed` |
| `recovery_exhausted` / `enable_quark_recovery` | 报告并停止；不要循环 `list/show` 试图自愈 |
| `organized` | 报告最终路径并停止 |
| `terminal: true` | 无条件停止 |

状态语义表：`{baseDir}/references/statuses.md`。工作流细节：`{baseDir}/references/workflow.md`。安全细则：`{baseDir}/references/safety.md`。

## 必须确认的时刻

- 多个 `candidateId` / `specificationGroups`：用户选，不得代选。
- `mediaType` 与 `finalPath`：必须来自 `plan download` 返回值；不确定 anime/drama 时先问（仅有 `SxxExx` 不能默认 drama）。
- 同一季多版本目录：用户选一版。
- 每次 `execute`（下载）、每次 `downloads recover execute`、每次 `organize execute`：对话确认后，命令必须带 `--confirmed`。脚本也会强制确认。
- 干跑 / “只要方案”：做到 `plan` 即停，禁止 `execute`。

## 轮询与重试预算

- `execute` 后立即 `downloads show` 一次。
- 同一轮对话最多共查询 **3** 次下载状态。
- `submitted` 且无 GID、无暂存文件：停止并说明转存可能未产生下载；**不要**自动再次 `execute`。
- `partial_failed` / `error`：停止；用户明确要求重试普通下载时，必须重新 `plan download`，不得复用旧 plan 盲重试。
- error 16 且 `confirm_recover`：只走 recover 计划/确认执行，不得用 `list/show` 触发副作用。

## 输出顺序

1. NAS 本地已有（路径、文件数、季集、画质）
2. 本地缺失或异常
3. 仅针对缺失的远端候选

本地结果必须排在远端候选之前。

## 总集数与目录完整性

总集数只能来自：用户明确提供、本地库元数据、候选 `tree`、或配置的权威元数据服务。不确定时只报告“已发现的集数范围”，不得宣称完整，不得凭标题常识编造 N 集。

仅当 `stats.truncated=false` 时可称完整目录树；`truncated=true` 时不得判断资源完整性。
