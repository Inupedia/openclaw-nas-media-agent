# 状态与 nextAction

以 `mediactl` JSON 的 `status` / `nextAction` / `notes` 为唯一事实来源。本表是解释层，不是第二套状态机。

## nextAction

| nextAction | 含义 |
| --- | --- |
| `stop_local_exists` | NAS 已有；停止联网 |
| `already_up_to_date` | 无缺集；不创建计划 |
| `choose_candidate` | 等待用户选候选 |
| `choose_tree_nodes` | 必须先选树节点 |
| `incremental_selection_unavailable` | 无法安全只选新增集 |
| `ready_to_organize` | 校验通过，可整理 |
| `quarantine_download` | 校验失败，进入/留在隔离 |
| `ready` | check-ready 通过 |

## 下载任务 status

| status | 含义 |
| --- | --- |
| `starting` / `submitted` | 已提交转存；**不代表** aria2 已开始或文件已落盘 |
| `active` / `waiting` / `paused` | 传输中 |
| `complete` | aria2 已下完到 `.incoming`；**还不在影视中心** |
| `partial_failed` | 同一任务部分 GID 完成、部分失败；不可 validate/organize |
| `error` | 传输失败 |
| `ready` | 校验通过，在 `.ready` |
| `quarantined` | 校验失败，在 `.quarantine`；修好后可再 validate |
| `organized` | 已进入正式库 |
| `cancelled` | 已取消 |

进入 `ready` / `quarantined` / `organized` 后，下载状态同步停止；`downloads list/show` 不得把它们写回 `complete`。

## 常见 notes

| note | 含义 |
| --- | --- |
| `transfer_idle` | QAS 已提交但无 aria2/文件 |
| `staging_only` | 文件在 `.incoming`，需 validate + organize |
| `staging_missing` | aria2 目标目录未创建 |
| `aria2_error_18` | Download aborted，常见于暂存目录不可写 |
| `aria2_error_16` | 已转存到夸克但 aria2 0 字节中止（常见缺 Cookie）；`downloads list/show` 会自动带 Cookie 重推（最多 2 次），勿反复 plan/execute；耗尽后用 `downloads recover TASK_ID` |
| `aria2_partial_failed` | 混合 GID 结果；不要当成完成 |

## 对用户表述

- `submitted` + 空 GID + 无暂存：说「转存可能没产生下载」，不要说「下载已开始」
- `complete`：说「已下到暂存区，尚未入库」
- `partial_failed`：说「部分失败，需处理后再继续」，不要只看某一个 GID
