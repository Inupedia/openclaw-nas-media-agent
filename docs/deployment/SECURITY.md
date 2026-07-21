# 部署安全边界

- 普通设置保存在 `deploy/config.yaml`；真实 secret 只保存在 `deploy/secrets/`。
- 计划、日志、备份 manifest 和报告不得包含真实 secret。
- 镜像使用 `repository@sha256:digest`，禁止 `latest`。
- 正式媒体库不得位于下载根目录内，也不得作为 aria2 下载目标。
- 权限优先采用同 UID、共享 GID 或 ACL；`0777` 只能作为托管下载区的显式兜底。
- 自动备份拒绝 `.env`、storage state、secret 路径及包含 secret sentinel 的文件。
- 用户原有容器、正式媒体文件和真实下载内容不会被自动删除。
- `full` 验收不会执行 `organize execute`。
