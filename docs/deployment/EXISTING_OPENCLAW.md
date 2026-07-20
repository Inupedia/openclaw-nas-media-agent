# 已有 OpenClaw 部署模式

`existing-openclaw` 只支持可明确识别的 Docker Compose 安装。部署器不会猜测未知容器，也不会改写原始 Compose 文件。

执行流程：

```bash
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
```

`discover` 是只读操作；`plan` 渲染固定 digest 的依赖 Compose 和路径配置，并生成 30 分钟有效的不可变计划。配置、secret 元数据、Docker 发现结果或托管文件发生变化后，旧计划会失效。

OpenClaw 的命令策略固定为 allowlist，只允许 Skill 中绝对路径的 `bin/mediactl`。`bash`、`sh`、`python`、`curl`、`rm` 和 `sudo` 不会加入 Agent 可执行列表。
