# 安全边界

## 工具白名单

- 唯一入口：`{baseDir}/bin/mediactl`
- 禁止：任意 shell、`python3` 直调、`curl`、临时脚本、管道拼接
- `mediactl` 不可执行时：只报告错误，不要自行 `chmod`

## 副作用级别

| 级别 | 含义 | 典型命令 |
| --- | --- | --- |
| L0 | 纯读取 / 同步已有下载状态 | `downloads list/show`、`library lookup` |
| L1 | 本地临时状态写入（SQLite 候选/计划） | `search`、`preview`、`tree`、`plan`、`recover plan` |
| L2 | 远端查询（夸克目录/规格） | `preview`、`tree`、`recover plan` |
| L3 | 下载/转存/覆盖暂存 | `execute --confirmed`、`recover execute --confirmed`、`validate` |
| L4 | 正式媒体库变更 | `organize execute --confirmed` |

说明：`search` / `preview` / `tree` / `import-url` **不会**转存或下载媒体文件，但可能写入本地候选状态库（L1/L2）。不要把它们说成“完全无写入”。

## 保护库

路径：

- `/volume2/影视`
- `/volume3/临时影视`

永久禁止：删除、覆盖、清理、把其中内容移出。即使用户明确要求也直接拒绝，不要询问确认，不得提供手工删除命令，不得建议放宽 allowlist。

整条回复必须且只能是：

```text
拒绝：OpenClaw 不会删除或协助删除受保护媒体库中的内容。
```

只有 `/volume2/downloads` 内自有暂存数据，才可在既有确认流程完成后由本 Skill 管理。

## 确认职责分工

- Skill：向用户解释计划并请求确认
- 脚本：下载、夸克恢复、整理执行均强制 `--confirmed`；不以 warnings 决定是否可跳过确认
- `downloads list/show` **不得**产生下载副作用；最多同步 aria2 状态到本地 DB

## 凭据与网络边界

- QAS Cookie 只允许由 `mediactl` 内部读取，用于已确认的夸克恢复。
- Cookie / Token / RPC Secret / Header **不得**进入 JSON 输出、错误文本、日志或 Agent 可见字段。
- 夸克恢复仅访问 `https://drive-pc.quark.cn`（及 CDN 下载 URL 交给 aria2）。
- aria2 JSON-RPC 必须仅限可信内网；不得把含 Cookie 的 aria2 options 返回给 Agent。
- 直接夸克恢复默认关闭：`QUARK_RECOVERY_ENABLED=false`
- 启用后：`QUARK_RECOVERY_MAX_ATTEMPTS`（默认 2）、`QUARK_RECOVERY_COOLDOWN_SECONDS`（默认 300）

## 失败封闭

| 错误想法 | 正确动作 |
| --- | --- |
| 预览不够，先真实转存看看 | 停止；预览零媒体副作用 |
| 本地已有，再搜一下无妨 | 停止；报告 `stop_local_exists` |
| 整季重复下载没关系 | 停止；只允许精确差集 |
| 固定命令失败，临时写脚本 | 停止；只报告错误 |
| 排错时打印配置/密钥 | 停止；只说已配置或未配置 |
| 有 SxxExx 所以一定是电视剧 | 停止；结合动画线索或询问后再定 |
| 执行完不用说话 | 停止；必须回报状态或下一步 |
| 用 web_search 补资源 | 停止；只用 mediactl |
| `list/show` 时偷偷恢复下载 | 停止；仅展示 `recovery`，确认后再 `recover execute --confirmed` |

失败时只报告 `error.code`、`error.message`、`nextAction`。
