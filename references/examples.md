# 示例

## 搜索动画

```text
{baseDir}/bin/mediactl search "作品名" --media-type anime
```

## 追更

```text
{baseDir}/bin/mediactl search "作品名" --media-type anime --update
```

## 用户粘贴夸克链接

```text
{baseDir}/bin/mediactl import-url "https://pan.quark.cn/s/xxxx"
{baseDir}/bin/mediactl tree CANDIDATE_ID
{baseDir}/bin/mediactl plan download CANDIDATE_ID --node NODE_ID --media-type anime
{baseDir}/bin/mediactl execute PLAN_ID --confirmed
```

## 下载完成后入库

```text
{baseDir}/bin/mediactl downloads show TASK_ID
{baseDir}/bin/mediactl downloads validate TASK_ID
{baseDir}/bin/mediactl organize plan TASK_ID
{baseDir}/bin/mediactl organize execute PLAN_ID --confirmed
```
