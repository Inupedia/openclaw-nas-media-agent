# PanSou Telegram Proxy and Official Source Coverage Design

## Goal

Restore Telegram-backed PanSou searches on the NAS while keeping the proxy
isolated to PanSou, preserving the current production service until a canary
passes, and matching the breadth of the official PanSou configuration.

This change does not modify OpenClaw, quark-auto-save, aria2, OpenList, or the
NAS-wide network route.

## Current State

- The production `pansou` container is healthy and reachable only through the
  NAS LAN address on port `8888`.
- Plugin searches return results, but Telegram-only searches return no results.
- The container resolves `t.me`, but direct TCP connections to Telegram time
  out.
- The NAS Tailscale service has no exit node configured.
- The current PanSou configuration enables 10 Telegram channels and 9 plugins.
- Two user-approved proxy nodes are available from a private subscription.

The root cause is Telegram network reachability, not PanSou API availability or
DNS resolution.

## Security Boundaries

Only PanSou may use the new proxy.

- Run a dedicated Mihomo container named `pansou-proxy`.
- Attach it only to the private Docker network shared with PanSou.
- Publish no proxy port on the NAS host.
- Do not set host-level, Docker-daemon-level, OpenClaw, aria2, QAS, or OpenList
  proxy variables.
- Store the extracted minimal Mihomo configuration in a root-only directory
  with directory mode `0700` and file mode `0600`.
- Do not retain the subscription response or subscription URL in the project,
  Compose file, logs, shell history, or test artifacts.
- The minimal configuration contains only the two approved nodes and a
  `url-test` group. It must not contain unrelated subscription nodes.
- Logs and validation reports must never print proxy server credentials,
  cookies, tokens, resource URLs, or complete HTTP headers.

## Proxy Selection

Mihomo will define one automatic group containing only the two approved nodes.
The group will periodically test connectivity and select the healthier node.
PanSou will use the proxy through its documented `PROXY` environment variable
over the private Docker network.

Before PanSou testing, the proxy must pass:

1. configuration validation;
2. container health and restart checks;
3. DNS resolution from the proxy container;
4. an HTTPS request to Telegram through the proxy.

A failure leaves production PanSou untouched.

## Official Search Sources

The supplied official configuration is the deployment baseline.

- Enable all 54 unique plugin names from the official `ENABLED_PLUGINS` list.
- Normalize the supplied 127 `CHANNELS` entries by removing the duplicate
  `ucshare`, resulting in 126 unique Telegram channels.
- Preserve channel spelling and case.
- Store the normalized lists in a non-secret PanSou environment file or Compose
  configuration so they are auditable.
- Do not scrape or automatically import future official-site configuration.
  Future source-list updates require an explicit review and canary test.

The proxy node configuration remains separate from these non-secret source
lists.

## Canary Architecture

Start a temporary `pansou-canary` container with:

- the same official PanSou image intended for production;
- all 126 unique Telegram channels;
- all 54 plugins;
- the private `pansou-proxy` endpoint;
- the same relevant performance settings as production;
- no public host port.

Test the canary from inside the Docker network. The existing `pansou` container
continues serving port `8888` throughout canary validation.

## Validation

Use the same search terms against the official public instance and the canary
within the same test window. Include at least:

- `凡人修仙传`;
- `庆余年`;
- `流浪地球2`.

For every term, test `src=tg`, `src=plugin`, and `src=all`. Record only status,
latency, source counts, cloud-type counts, and deduplicated result counts.

The canary passes only when:

- its health endpoint is stable;
- Telegram-only searches return results;
- plugin-only searches return results;
- combined searches return results without fatal worker errors;
- the primary `凡人修仙传` comparison reaches at least 70% of the official
  instance's deduplicated Quark result count;
- combined deduplicated coverage reaches at least 60% of the official
  instance for the same test window;
- the proxy has no host-published port;
- no non-PanSou container has acquired proxy environment variables or changed
  networking;
- repeated searches do not cause a crash or restart loop.

Exact equality with the official result count is not required because Telegram
posts, plugin indexes, caches, and asynchronous responses change continuously.

## Production Cutover

After a successful canary:

1. save the current production container inspection data and configuration;
2. stop and rename the old container as a timestamped rollback container;
3. start the validated PanSou configuration with the original `pansou` name,
   LAN-only port binding, and Docker network;
4. verify health, API access, Telegram search, plugin search, and combined
   search through the production endpoint;
5. retain the stopped rollback container until the user explicitly approves
   its later removal.

No media files are created, moved, or deleted by this change.

## Failure and Rollback

If proxy validation, canary coverage, or production verification fails:

- stop the failed canary or replacement container;
- restore the original container name and start it;
- verify the original plugin-backed API is reachable on port `8888`;
- leave proxy diagnostics limited to non-secret error summaries;
- do not retry indefinitely or create a background retry task.

The rollback procedure must never delete the original container, its mounted
data, or any media directory.

## Operational Outcome

After deployment, OpenClaw continues to call the same NAS PanSou endpoint. The
change is internal to PanSou: searches gain the official breadth of Telegram
channels and plugins, while only PanSou traffic can traverse the dedicated
proxy. Existing incremental-download, preview, confirmation, and media-library
safety rules remain unchanged.

## Appendix: Normalized Official Source Lists

The 54 plugins, in supplied order:

```text
ddys,erxiao,jutoushe,labi,libvio,panta,susu,wanou,xuexizhinan,zhizhen,ahhhhfs,clxiong,discourse,djgou,duoduo,hdmoli,huban,leijing,muou,nsgame,ouge,panyq,shandian,xinjuc,yunsou,aikanzy,bixin,cldi,clmao,cyg,fox4k,gying,haisou,hunhepan,jikepan,miaoso,nyaa,pansearch,panwiki,pianku,qupanshe,qupansou,sdso,thepiratebay,wuji,xb6v,xdpan,xdyh,xiaoji,xiaozhang,xys,yuhuage,javdb,u3c3
```

The 126 unique channels, in supplied order with the second `ucshare` entry
removed:

```text
Aliyun_4K_Movies,bdbdndn11,yunpanx,bsbdbfjfjff,yp123pan,sbsbsnsqq,yunpanxunlei,tianyifc,BaiduCloudDisk,txtyzy,peccxinpd,gotopan,PanjClub,kkxlzy,baicaoZY,MCPH01,MCPH02,MCPH03,bdwpzhpd,ysxb48,jdjdn1111,yggpan,MCPH086,zaihuayun,Q66Share,ucwpzy,shareAliyun,alyp_1,dianyingshare,Quark_Movies,XiangxiuNBB,ydypzyfx,ucquark,xx123pan,yingshifenxiang123,zyfb123,tyypzhpd,tianyirigeng,cloudtianyi,hdhhd21,Lsp115,oneonefivewpfx,qixingzhenren,taoxgzy,Channel_Shares_115,tyysypzypd,vip115hot,wp123zy,yunpan139,yunpan189,yunpanuc,yydf_hzl,leoziyuan,Q_dongman,yoyokuakeduanju,TG654TG,WFYSFX02,QukanMovie,yeqingjie_GJG666,movielover8888_film3,Baidu_netdisk,D_wusun,FLMdongtianfudi,KaiPanshare,QQZYDAPP,rjyxfx,PikPak_Share_Channel,btzhi,newproductsourcing,cctv1211,duan_ju,QuarkFree,yunpanNB,kkdj001,xxzlzn,pxyunpanxunlei,jxwpzy,kuakedongman,liangxingzhinan,xiangnikanj,solidsexydoll,guoman4K,zdqxm,kduanju,cilidianying,CBduanju,SharePanFilms,dzsgx,BooksRealm,Oscar_4Kmovies,douerpan,baidu_yppan,Q_jilupian,Netdisk_Movies,yunpanquark,ammmziyuan,ciliziyuanku,cili8888,jzmm_123pan,Q_dianying,domgmingapk,dianying4k,q_dianshiju,tgbokee,ucshare,godupan,gokuapan,gimy115,WFYSFX03,peccxin,Movie888035,xlwpzy,zyywpzy,wydwpzy,gimy100,gimy115iso,aliyunys,clouddriveresources,XunLeiPinDao,ydwpzy,a123fxme,WPpindao,kuyupan,djya5,pan_guangya,tgsearchers6
```
