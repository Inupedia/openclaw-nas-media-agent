# PanSou Telegram Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore PanSou Telegram search on the NAS through a PanSou-only proxy and deploy the official 126-channel/54-plugin source set without interrupting the current service until validation passes.

**Architecture:** A private Mihomo container provides an unexposed SOCKS5 endpoint to a temporary PanSou canary on the existing `pansou-network`. The canary uses the normalized official source lists and is compared with the official public instance; only a passing canary is promoted to the existing LAN-only production endpoint.

**Tech Stack:** Docker Engine, Docker Compose, Mihomo, official PanSou image, POSIX shell, Python 3 with PyYAML, SSH/SFTP.

## Global Constraints

- Only PanSou may use the proxy; do not change NAS, Docker daemon, OpenClaw, aria2, QAS, OpenList, or Tailscale proxy/routing settings.
- Do not publish the Mihomo proxy port on the NAS host.
- The proxy directory must be mode `0700`; its generated configuration must be mode `0600`.
- Never persist or print the private subscription URL, proxy credentials, cookies, tokens, resource URLs, or complete HTTP headers.
- Enable exactly the 126 unique channels and 54 unique plugins in the approved design appendix.
- Keep the current production PanSou container unchanged until the canary passes.
- Do not delete the old production container, rollback data, or any media file.
- Do not create automatic retries, schedulers, or background monitoring.

---

### Task 1: Capture Production State and Establish Rollback

**Files:**
- Create on NAS: `$BACKUP/container-inspect.json`
- Create on NAS: `$BACKUP/network-inspect.json`
- Create on NAS: `$BACKUP/baseline-summary.txt`

**Interfaces:**
- Consumes: running Docker container named `pansou`
- Produces: timestamped rollback directory and verified production baseline

- [ ] **Step 1: Run read-only preflight**

```sh
set -eu
docker inspect pansou --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker inspect pansou --format '{{json .HostConfig.PortBindings}}'
docker inspect pansou --format '{{json .NetworkSettings.Networks}}'
docker network inspect pansou-network --format '{{.Name}}'
```

Expected: `pansou` is `running healthy`, port `8888/tcp` is bound only to
`192.168.31.242`, and `pansou-network` exists.

- [ ] **Step 2: Create the protected backup directory**

```sh
set -eu
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/volume4/docker/pansou-backups/$STAMP"
install -d -m 0700 "$BACKUP"
printf '%s\n' "$BACKUP"
```

Expected: one absolute backup path under `/volume4/docker/pansou-backups`.
Retain this path for every later rollback command.

- [ ] **Step 3: Capture container and network state**

```sh
set -eu
docker inspect pansou > "$BACKUP/container-inspect.json"
docker network inspect pansou-network > "$BACKUP/network-inspect.json"
{
  docker inspect pansou --format 'image={{.Config.Image}}'
  docker inspect pansou --format 'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}}'
  docker inspect pansou --format 'ports={{json .HostConfig.PortBindings}}'
  docker inspect pansou --format 'networks={{json .NetworkSettings.Networks}}'
} > "$BACKUP/baseline-summary.txt"
chmod 0600 "$BACKUP"/*
```

Expected: all three files exist, are non-empty, and have mode `0600`.

- [ ] **Step 4: Verify no unrelated container currently has the planned proxy endpoint**

```sh
docker inspect openclaw quark-auto-save aria2 2>/dev/null \
  --format '{{.Name}} {{range .Config.Env}}{{println .}}{{end}}' |
  grep -E 'pansou-proxy:7890|^(HTTP|HTTPS|ALL)_PROXY=' && exit 1 || true
```

Expected: no matches.

### Task 2: Generate and Validate the Private Mihomo Service

**Files:**
- Create on NAS: `/volume4/docker/pansou-proxy/compose.yaml`
- Create on NAS: `/volume4/docker/pansou-proxy/config/config.yaml`

**Interfaces:**
- Consumes: private subscription supplied by the user in process memory
- Produces: healthy `pansou-proxy` container on `pansou-network`, with SOCKS5 at `pansou-proxy:7890`

- [ ] **Step 1: Create protected directories**

```sh
install -d -m 0700 /volume4/docker/pansou-proxy
install -d -m 0700 /volume4/docker/pansou-proxy/config
```

Expected: both directories have mode `0700`.

- [ ] **Step 2: Fetch and reduce the subscription in memory**

Upload this non-secret extractor to
`/volume4/docker/pansou-proxy/extract_config.py` with mode `0700`. Run it over
an SSH channel and send the already supplied subscription URL as the only line
on standard input. Do not put the URL in an argument, environment variable,
file, log, or shell command:

```python
import os
import sys
import urllib.request
import yaml

TARGET = "/volume4/docker/pansou-proxy/config/config.yaml"
NAMES = ["inupedia - oracle3 - ss", "inupedia - oracle4 -ss"]

url = sys.stdin.readline().strip()
if not url:
    raise SystemExit("subscription URL missing")

request = urllib.request.Request(url, headers={"User-Agent": "mihomo"})
with urllib.request.urlopen(request, timeout=30) as response:
    source = yaml.safe_load(response.read())

by_name = {
    item.get("name"): item
    for item in source.get("proxies", [])
    if isinstance(item, dict)
}
missing = [name for name in NAMES if name not in by_name]
if missing:
    raise SystemExit("approved proxy nodes missing")

config = {
    "mixed-port": 7890,
    "allow-lan": True,
    "bind-address": "*",
    "mode": "rule",
    "log-level": "warning",
    "ipv6": False,
    "proxies": [by_name[name] for name in NAMES],
    "proxy-groups": [{
        "name": "TG-PROXY",
        "type": "url-test",
        "proxies": NAMES,
        "url": "https://www.gstatic.com/generate_204",
        "interval": 300,
    }],
    "rules": ["MATCH,TG-PROXY"],
}

flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
fd = os.open(TARGET, flags, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as output:
    yaml.safe_dump(config, output, allow_unicode=True, sort_keys=False)
os.chmod(TARGET, 0o600)
```

Expected: exit code `0`, a non-empty mode-`0600` target file, and no secret
output. Remove only the non-secret extractor after configuration validation;
retain the generated target because Mihomo requires it.

- [ ] **Step 3: Write the proxy Compose definition**

```yaml
services:
  pansou-proxy:
    image: ghcr.io/metacubex/mihomo:latest
    container_name: pansou-proxy
    command: ["-d", "/root/.config/mihomo"]
    volumes:
      - ./config:/root/.config/mihomo:ro
    networks:
      - pansou-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "/mihomo", "-t", "-d", "/root/.config/mihomo"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

networks:
  pansou-network:
    external: true
```

Save as `/volume4/docker/pansou-proxy/compose.yaml` with mode `0600`.

- [ ] **Step 4: Validate configuration before starting**

```sh
docker run --rm \
  -v /volume4/docker/pansou-proxy/config:/root/.config/mihomo:ro \
  ghcr.io/metacubex/mihomo:latest \
  -t -d /root/.config/mihomo
```

Expected: configuration test succeeds without printing proxy credentials.

- [ ] **Step 5: Start and test the proxy**

```sh
docker compose -f /volume4/docker/pansou-proxy/compose.yaml up -d
docker inspect pansou-proxy --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} {{json .HostConfig.PortBindings}}'
docker run --rm --network pansou-network curlimages/curl:latest \
  --silent --show-error --fail --max-time 20 \
  --socks5-hostname pansou-proxy:7890 \
  -o /dev/null https://t.me/
```

Expected: proxy is running/healthy, `PortBindings` is `{}`, and Telegram HTTPS
is reachable.

### Task 3: Build and Validate the Official-Source Canary

**Files:**
- Create on NAS: `/volume4/docker/pansou-canary/pansou.env`
- Create on NAS: `/volume4/docker/pansou-canary/compose.yaml`
- Create on NAS: `/volume4/docker/pansou-canary/validation-summary.json`

**Interfaces:**
- Consumes: `pansou-proxy:7890`, official source lists from the approved design
- Produces: pass/fail canary report without resource links

- [ ] **Step 1: Generate and validate `pansou.env`**

Generate the environment bytes locally from the two exact fenced lists in
`docs/superpowers/specs/2026-07-18-pansou-telegram-proxy-design.md`:

```python
import re
from pathlib import Path

spec = Path(
    "docs/superpowers/specs/"
    "2026-07-18-pansou-telegram-proxy-design.md"
).read_text(encoding="utf-8")
blocks = re.findall(r"```text\s*(.*?)\s*```", spec, flags=re.S)
plugins = blocks[-2].strip().split(",")
channels = blocks[-1].strip().split(",")
assert len(plugins) == len(set(plugins)) == 54
assert len(channels) == len(set(channels)) == 126

env_bytes = (
    "PORT=8888\n"
    "PROXY=socks5://pansou-proxy:7890\n"
    "AUTH_ENABLED=false\n"
    f"CHANNELS={','.join(channels)}\n"
    f"ENABLED_PLUGINS={','.join(plugins)}\n"
).encode("utf-8")
```

Upload `env_bytes` through SFTP to
`/volume4/docker/pansou-canary/pansou.env`, and set mode `0600`.

- [ ] **Step 2: Write the canary Compose definition**

```yaml
services:
  pansou-canary:
    image: ghcr.io/fish2018/pansou:latest
    container_name: pansou-canary
    env_file:
      - ./pansou.env
    networks:
      - pansou-network
    restart: "no"

networks:
  pansou-network:
    external: true
```

Save as `/volume4/docker/pansou-canary/compose.yaml` with mode `0600`. It must
contain no `ports` entry.

- [ ] **Step 3: Start the canary and verify isolation**

```sh
docker compose -f /volume4/docker/pansou-canary/compose.yaml up -d
docker inspect pansou-canary --format '{{.State.Status}} {{json .HostConfig.PortBindings}}'
docker inspect pansou --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
```

Expected: canary is running with `{}` port bindings; production remains
running/healthy.

- [ ] **Step 4: Run source-specific test queries**

For each keyword `凡人修仙传`, `庆余年`, and `流浪地球2`, request `res=all` with
each source `tg`, `plugin`, and `all` from:

```text
http://pansou-canary:8888/api/search
https://so.252035.xyz/api/search
```

Run the local requests from a disposable curl container on `pansou-network`.
Parse responses in memory. Write only this shape to
`/volume4/docker/pansou-canary/validation-summary.json`:

```json
{
  "tested_at": "ISO-8601 timestamp",
  "queries": [
    {
      "keyword": "凡人修仙传",
      "source": "tg",
      "canary_status": 200,
      "official_status": 200,
      "canary_total": 1,
      "official_total": 1,
      "canary_deduplicated": 1,
      "official_deduplicated": 1,
      "canary_quark_deduplicated": 1,
      "official_quark_deduplicated": 1,
      "canary_latency_ms": 1,
      "official_latency_ms": 1
    }
  ]
}
```

The numeric `1` values illustrate required numeric fields; populate them with
measured counts. Do not store titles, links, contents, images, or headers.

- [ ] **Step 5: Apply the acceptance gate**

Pass only if:

```text
health stable
AND every canary HTTP status is 200
AND every tg/plugin/all class returns at least one result across the test set
AND 凡人修仙传 canary Quark deduplicated / official Quark deduplicated >= 0.70
AND 凡人修仙传 canary combined deduplicated / official combined deduplicated >= 0.60
AND pansou-canary has no published port
AND pansou is still running and healthy
AND pansou-canary and pansou-proxy have no restart loop
```

Expected: print only `CANARY_PASS` or a concise non-secret failed condition.
On failure, stop `pansou-canary` and do not continue.

### Task 4: Promote the Validated Configuration with Automatic Rollback

**Files:**
- Create on NAS: `/volume4/docker/pansou/compose.yaml`
- Create on NAS: `/volume4/docker/pansou/pansou.env`

**Interfaces:**
- Consumes: passing canary configuration and Task 1 rollback path
- Produces: production `pansou` on the original LAN-only endpoint

- [ ] **Step 1: Prepare production files**

Copy the validated canary environment file to
`/volume4/docker/pansou/pansou.env` and set mode `0600`. Write:

```yaml
services:
  pansou:
    image: ghcr.io/fish2018/pansou:latest
    container_name: pansou
    env_file:
      - ./pansou.env
    ports:
      - "192.168.31.242:8888:8888"
    networks:
      - pansou-network
    restart: unless-stopped

networks:
  pansou-network:
    external: true
```

Save it as `/volume4/docker/pansou/compose.yaml` with mode `0600`.

- [ ] **Step 2: Reconfirm the canary gate immediately before cutover**

```sh
test -s /volume4/docker/pansou-canary/validation-summary.json
docker inspect pansou-canary --format '{{.State.Status}}'
docker inspect pansou-proxy --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker inspect pansou --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
```

Expected: canary running, proxy healthy, production healthy.

- [ ] **Step 3: Perform the reversible cutover**

```sh
set -eu
ROLLBACK_NAME="pansou-rollback-$(date +%Y%m%d-%H%M%S)"
docker stop pansou
docker rename pansou "$ROLLBACK_NAME"
docker rm -f pansou-canary
if ! docker compose -f /volume4/docker/pansou/compose.yaml up -d; then
  docker rm -f pansou 2>/dev/null || true
  docker rename "$ROLLBACK_NAME" pansou
  docker start pansou
  exit 1
fi
printf '%s\n' "$ROLLBACK_NAME" > "$BACKUP/rollback-container-name.txt"
chmod 0600 "$BACKUP/rollback-container-name.txt"
```

Expected: new container is named `pansou`; old container remains stopped under
the timestamped rollback name.

- [ ] **Step 4: Verify production or roll back**

Check health, LAN-only port binding, `src=tg`, `src=plugin`, and `src=all`. If
any check fails:

```sh
set -eu
docker rm -f pansou
docker rename "$ROLLBACK_NAME" pansou
docker start pansou
docker inspect pansou --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
exit 1
```

Expected on success: production is healthy on
`http://192.168.31.242:8888/`, Telegram returns results, and the rollback
container remains stopped. Expected on failure: original production is
restored and healthy.

### Task 5: Post-Cutover Safety Audit and Handoff

**Files:**
- Modify on NAS: `/volume4/docker/pansou-canary/validation-summary.json`
- Preserve on NAS: `$BACKUP/`

**Interfaces:**
- Consumes: promoted production service
- Produces: concise deployment report and retained rollback path

- [ ] **Step 1: Audit network scope**

```sh
docker inspect pansou-proxy --format 'ports={{json .HostConfig.PortBindings}} networks={{json .NetworkSettings.Networks}}'
docker inspect pansou --format 'ports={{json .HostConfig.PortBindings}} networks={{json .NetworkSettings.Networks}}'
docker inspect openclaw quark-auto-save aria2 2>/dev/null \
  --format '{{.Name}} {{range .Config.Env}}{{println .}}{{end}}' |
  grep -E 'pansou-proxy:7890|^(HTTP|HTTPS|ALL)_PROXY=' && exit 1 || true
```

Expected: proxy has no host port; PanSou alone references the proxy; production
binds only `192.168.31.242:8888`.

- [ ] **Step 2: Verify normalized source counts**

```sh
docker inspect pansou --format '{{range .Config.Env}}{{println .}}{{end}}' |
  grep -E '^(CHANNELS|ENABLED_PLUGINS)='
```

Parse without printing full values in the final report. Expected: 126 unique
channels and 54 unique plugins.

- [ ] **Step 3: Confirm rollback retention**

```sh
ROLLBACK_NAME="$(cat "$BACKUP/rollback-container-name.txt")"
docker inspect "$ROLLBACK_NAME" --format '{{.Name}} {{.State.Status}}'
find "$BACKUP" -maxdepth 1 -type f -printf '%f %s bytes\n'
```

Expected: rollback container exists and is stopped; all backup files are
non-empty.

- [ ] **Step 4: Report only verified outcomes**

Report:

```text
proxy container health and absence of host ports
production PanSou health and LAN endpoint
126 unique channels / 54 unique plugins
tg/plugin/all test status and count ratios
rollback container name and backup directory
confirmation that unrelated containers and media directories were unchanged
```

Do not report source links, proxy credentials, the subscription URL, cookies,
tokens, or complete environment values.
