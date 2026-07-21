# PanSou 与代理

支持三种模式：

- `none`：主机可直接访问 Telegram；
- `existing`：在 `pansou_proxy_url` secret 文件中提供 HTTP、HTTPS 或 SOCKS5 URL；
- `managed`：在 `singbox_config.json` secret 文件中提供完整、合法的 sing-box JSON。

托管代理只在 `openclaw-media` Docker 网络提供 1080 端口，不发布宿主机端口，也不附带代理供应商、账号、节点或订阅转换服务。

PanSou 服务可用但 Telegram 来源不可达时，状态为 `degraded`，QAS 直接链接和本地媒体流程仍可使用。
