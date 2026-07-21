# 部署排错

所有命令只输出一个 JSON 文档。优先查看：

- `status`：`ready`、`degraded`、`manual_action_required`、`failed` 或 `rolled_back`；
- `nextAction`：下一步确定动作；
- `errors[].code`：稳定错误码；
- `deploy/runtime/reports/`：脱敏报告；
- `deploy/runtime/journals/`：事务断点；
- `deploy/runtime/backups/`：回滚清单。

常用检查：

```bash
python3 deploy/cli.py versions check
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py verify --level safe
```

不要通过打印完整容器环境变量排错。不要手工修改 `plan.json` 或 journal。
