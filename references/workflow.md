# 工作流

## 副作用分层

| 阶段 | 命令 | 副作用 |
| --- | --- | --- |
| 本地/远端查询 | `library` / `search` | 无写入 |
| 导入分享 | `import-url` / `share open` / `search <url>` | 仅写入候选状态库 |
| 预览 | `preview` | 无转存 |
| 目录树 | `tree` | 无转存 |
| 下载计划 | `plan download` | 只生成计划 |
| 下载执行 | `execute ... --confirmed` | QAS 转存 + aria2 下载到 `.incoming` |
| 校验 | `downloads validate` | 迁移到 `.ready` / `.quarantine` |
| 整理计划 | `organize plan` | 只生成计划 |
| 整理执行 | `organize execute ... --confirmed` | 进入正式媒体库 |

## 标准路径

### 普通搜索

1. `search "作品名" [--media-type ...]`
2. 若 `stop_local_exists` / `already_up_to_date` → 停止
3. 展示候选与 `specificationGroups`，等用户选 `candidateId`
4. `tree CANDIDATE_ID` → 解释树 → 建议 `nodeId`
5. 用户确认类型与节点后 `plan download ... --node ... [--media-type ...]`
6. 用户确认后 `execute PLAN_ID --confirmed`
7. 按状态机监控 → `validate` → `organize`

### 夸克链接

用户粘贴 `https://pan.quark.cn/s/...` 时：

1. `import-url` / `search <url>` / `share open` / `tree <url>` 得到 `candidateId`
2. 不要用网页抓取或 `web_search` 代替
3. 之后与普通下载相同：解释树 → 确认 → plan → execute

### 追更 / 补集

仅当用户说更新、追更、补集、缺集时加 `--update`。

- 更新结果只能包含本地缺少、且不在下载中/待执行计划里的集
- `incremental_selection_unavailable`：停止，让用户决定，不要转存全集
- 缺集可对比同次搜索其他候选的树覆盖，给出「主源 + 补源」方案；仍不要自动 execute

### 只读 / 干跑

用户说搜索、看看、预览、推荐、有什么可看、不要真下载、只要方案时：

- 做到 `search` / `tree` / `plan` 即可
- 禁止 `execute`
- 不要主动说「确认后我将执行」

## 选片规则

- 未看 `tree`、未传 `--node` 时不得 `plan download`
- 不要套用固定文件夹名规则；按用户请求与树结构判断
- 默认只要正片视频；除非用户明确要求，不选小说/漫画/花絮/特典图包
- 用户只要某一季时，只选该季 `nodeId`，禁止图省事全下
- 同一季多压制组/画质：必须先问用户要哪一版

## 媒体类型

`--media-type`：`movie` | `drama` | `tv` | `anime` | `documentary` | `show` | `other`

- 电视剧用 `drama`；动画/番剧用 `anime`
- `tv` 仅旧任务兼容
- HiveWeb / Baha / VCB / ANi / LoliHouse / 繁日 / 动画等线索优先 `anime`
- 仅有 `SxxExx` 不能默认真人剧
- 不确定时先问用户

## 规格组

返回 `specificationGroups` 时，把所有不同规格列给用户。每组尽量报告：分辨率、HDR/DV、编码、音频、字幕、总大小、文件数、季集范围。`中英双语`字幕可标记优选，但**不得自动选择**。

`discoverySources`（`jiaofu` / `qas` / `pansou`）只说明发现来源，不能作为自动选择依据。`jiaofu_unavailable` / `pansou_unavailable` 时简短说明并继续展示已有候选。

## 落库路径

`finalPath` 只能来自 `plan download` 返回值（如 Anime → routing 中的 Anime 根，Movie → `/volume3/临时影视/Movie/...`）。禁止臆造不在 `routing.json` 中的目录。
