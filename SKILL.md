---
name: resource-download-agent
description: Use when 用户要求搜索、查找、预览、推荐或下载电影、电视剧、动画、动漫、综艺、纪录片等影视资源，或要求追更、补集、检查更新、换版本、查看下载状态、暂停、继续、取消、删除、校验、整理、释放空间、压缩或转码。
metadata: {"openclaw":{"primaryEnv":"QAS_TOKEN","requires":{"env":["QAS_BASE_URL","QAS_TOKEN","ARIA2_RPC_URL","ARIA2_RPC_SECRET","RESOURCE_AGENT_STATE_DB"]},"envVars":[{"name":"QAS_BASE_URL","required":true,"description":"QAS API endpoint"},{"name":"QAS_TOKEN","required":true,"description":"QAS API credential"},{"name":"ARIA2_RPC_URL","required":true,"description":"aria2 RPC endpoint"},{"name":"ARIA2_RPC_SECRET","required":true,"description":"aria2 RPC credential"},{"name":"RESOURCE_AGENT_STATE_DB","required":true,"description":"Agent state database path"}]}}
---

# Resource Download Agent

只处理影视资源与 NAS 媒体库任务。所有系统操作只调用：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl
```

## 核心约束

- 对作品或资源的询问，先调用 `search`。程序会先查 NAS 本地，再决定是否搜索远端。
- JSON 返回 `terminal: true` 时，立即报告结果并停止所有工具调用。
- `nextAction: stop_local_exists` 表示 NAS 已有该作品；不要联网、不要继续找远端版本。
- `nextAction: already_up_to_date` 表示本地没有缺集；不要创建计划。
- 用户说“更新、追更、补集、缺集”时才添加 `--update`。
- 更新结果只能包含本地缺少、且不在下载中或待执行计划里的集。
- `nextAction: incremental_selection_unavailable` 表示远端不能安全地只选新增集；停止并让用户决定，不要转存全集。
- 只说“搜索、看看、预览、推荐、有什么可看”时，绝不创建或执行下载计划。

## 输出顺序

任何资源答复按这个顺序组织：

1. NAS 本地已有资源、路径、文件数、季集和画质。
2. 本地缺失或异常的季集。
3. 仅针对缺失内容的远端候选。

NAS 本地结果必须排在远端候选之前。

## 搜索与预览

普通搜索：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl search "作品名" --media-type anime
```

检查更新：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl search "作品名" --media-type anime --update
```

`--media-type` 只能是 `movie`、`drama`、`tv`、`anime`、`documentary`、`show` 或 `other`。电视剧优先使用 `drama`；`tv` 只为旧任务兼容。不确定类型时省略，不要猜。

搜索返回 `specificationGroups` 时，必须把所有不同规格列给用户选择。每组至少报告可用的分辨率、HDR/Dolby Vision、视频编码、音频、字幕、总大小、文件数和季集范围。`中英双语`字幕排在同等候选前面并标记优选，但不得自动选择候选、不得只展示评分最高或文件最大的版本。用户必须从已展示的 `candidateId` 中选择；选择仍不明确时继续询问。

预览候选：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl preview CANDIDATE_ID
```

只使用 JSON 返回的 `candidateId`。不要使用或索取底层分享链接。

## 下载

先预览，再用候选 ID 生成计划：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl plan download CANDIDATE_ID
```

向用户报告计划中的作品、集数、下载区、最终目录、冲突和副作用。只有用户明确要求下载，且计划不要求额外确认时，才可以执行：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl execute PLAN_ID
```

计划要求确认时，先取得当前用户明确确认，再执行：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl execute PLAN_ID --confirmed
```

所有内容先下载到 `/volume2/downloads/.incoming/<task-id>`。QAS 和 aria2 不得直接把 `/volume2/影视` 或 `/volume3/临时影视` 作为下载目标。

## 下载状态与控制

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl downloads list
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl downloads show TASK_ID
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl downloads pause TASK_ID
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl downloads resume TASK_ID
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl downloads cancel TASK_ID
```

只能控制 JSON 中出现的自有 `taskId`。取消默认保留已下载数据；不要顺带清理文件。

`/volume2/影视` 和 `/volume3/临时影视` 是永久保护库。永远不得删除、覆盖、清理其中内容，也不得把其中内容移动出去；即使用户明确要求也必须直接拒绝，不要询问确认。不得提供手工删除命令，不得建议放宽 allowlist、安全策略或启用其他执行能力。拒绝后立即停止，不要提供替代删除途径或下一步。整条回复必须且只能是：“拒绝：OpenClaw 不会删除或协助删除受保护媒体库中的内容。”只有 `/volume2/downloads` 内的自有暂存数据，才可在既有确认流程完成后移除。

## 校验与整理

下载完成后先校验：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl downloads validate TASK_ID
```

只有 `nextAction: ready_to_organize` 才能生成整理计划：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl organize plan TASK_ID
```

整理会把校验通过的文件从 `/volume2/downloads` 转移到正式媒体库。它始终需要当前用户单独确认：

```text
/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl organize execute PLAN_ID --confirmed
```

目标已存在、文件未完成、存在临时文件、视频不可读或校验失败时停止。不要覆盖，不要自行删除源文件。

## 失败处理

- `mediactl` 失败时，只报告 `error.code`、`error.message` 和 `nextAction`。
- 不要改用其他命令、临时脚本、底层 HTTP 请求或直接调用 QAS/aria2。
- 不要用宽泛目录列表代替 `library`、`search` 或 `downloads` 命令。
- 终止结果之后不要调用联网搜索来“补充资源”。
- 不要输出 Cookie、Token、RPC Secret、Authorization、完整 Header、环境变量或底层原始响应。

## 红线

出现以下想法时立即停止：

| 想法 | 正确动作 |
|---|---|
| “预览接口不够，我先真实转存再看看” | 停止；预览必须零副作用 |
| “本地已有，但再搜一下也无妨” | 停止；报告 `stop_local_exists` |
| “整季分享里包含新集，重复下载没关系” | 停止；只允许精确差集 |
| “固定命令失败，我临时写个脚本” | 停止；只报告错误 |
| “为了排错，把配置结果打印出来” | 停止；只报告已配置或未配置 |

最终答复用简洁中文，先给状态和结论，再给下一步。
