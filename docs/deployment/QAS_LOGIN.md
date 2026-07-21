# QAS 初始化与登录

部署器仅支持版本锁中验证过的 QAS 配置结构。它会备份配置，只写入已验证的 WebUI、Cookie 和 aria2 插件字段，并通过 `/data?token=...` 回读。

QAS API Token 由当前 WebUI 用户名和密码按锁定版本规则派生。不要自行填写不匹配的 Token。

出现登录、扫码、验证码或未知界面时，部署器返回 `manual_action_required`，不会绕过身份验证。完成操作后按报告中的部署 ID 恢复：

```bash
python3 deploy/cli.py apply --resume DEPLOYMENT_ID --confirmed
```
