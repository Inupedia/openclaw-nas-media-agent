# 快速部署

首阶段适用于已经安装 OpenClaw 的绿联 UGOS 或标准 Linux Docker 主机。

```bash
python3 deploy/cli.py init
python3 deploy/cli.py discover
python3 deploy/cli.py plan
python3 deploy/cli.py apply --plan-id PLAN_ID --confirmed
python3 deploy/cli.py verify --level safe
```

`init` 只生成 `deploy/config.yaml` 和空的私密文件，不启动容器。真实密码、Cookie、Token 和代理配置写入 `deploy/secrets/` 对应文件；目录权限为 `0700`，文件为 `0600`。

首次部署可能返回 `manual_action_required`。登录、扫码、验证码、真实下载确认以及冲突选择必须由用户完成，然后使用返回的 `nextAction` 继续。

真实小文件验收需要同时满足配置开关、合法测试链接和命令确认：

```bash
python3 deploy/cli.py verify --level full --confirmed
```

完整验收只生成整理计划，不执行正式入库。
