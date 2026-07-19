# mediactl 命令

入口：`{baseDir}/bin/mediactl`

## 就绪检查

```text
{baseDir}/bin/mediactl check-ready
```

## 本地库

```text
{baseDir}/bin/mediactl library lookup "作品名" [--media-type anime]
```

## 搜索与导入

```text
{baseDir}/bin/mediactl search "作品名" --media-type anime
{baseDir}/bin/mediactl search "作品名" --media-type anime --update
{baseDir}/bin/mediactl import-url "https://pan.quark.cn/s/xxxx"
{baseDir}/bin/mediactl search "https://pan.quark.cn/s/xxxx"
{baseDir}/bin/mediactl share open "https://pan.quark.cn/s/xxxx"
```

## 预览与目录树

```text
{baseDir}/bin/mediactl preview CANDIDATE_ID
{baseDir}/bin/mediactl tree CANDIDATE_ID
{baseDir}/bin/mediactl tree "https://pan.quark.cn/s/xxxx"
```

只使用 JSON 返回的 `candidateId` 与 `nodeId`。不要索取底层分享链接。

## 下载计划与执行

```text
{baseDir}/bin/mediactl plan download CANDIDATE_ID --node NODE_ID [--node NODE_ID ...] [--media-type anime]
{baseDir}/bin/mediactl execute PLAN_ID --confirmed
```

未提供 `--node` 时不得猜测或自动全选。下载执行**始终**需要 `--confirmed`。

内容进入：`/volume2/downloads/.incoming/<task-id>`。QAS/aria2 不得把正式库当作下载目标。

## 下载状态与控制

```text
{baseDir}/bin/mediactl downloads list
{baseDir}/bin/mediactl downloads show TASK_ID
{baseDir}/bin/mediactl downloads pause TASK_ID
{baseDir}/bin/mediactl downloads resume TASK_ID
{baseDir}/bin/mediactl downloads cancel TASK_ID
{baseDir}/bin/mediactl downloads validate TASK_ID
```

`list/show` 只同步并报告状态（L0），**不会**自动重推下载。

## 夸克恢复（error 16）

需环境变量 `QUARK_RECOVERY_ENABLED=true`。

```text
{baseDir}/bin/mediactl downloads recover plan TASK_ID
{baseDir}/bin/mediactl downloads recover execute PLAN_ID --confirmed
```

仅当 `recovery.eligible=true`（通常 `status=error|partial_failed` 且仅 error 16、暂存无有效文件）时可以 plan。执行必须 `--confirmed`。

## 整理

```text
{baseDir}/bin/mediactl organize plan TASK_ID
{baseDir}/bin/mediactl organize execute PLAN_ID --confirmed
```

目标已存在、未完成、临时文件、不可读或校验失败时停止。不要覆盖，不要自行删除源文件。
