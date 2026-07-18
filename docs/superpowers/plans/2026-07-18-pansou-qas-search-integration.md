# PanSou and QAS Search Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PanSou discovery and sequel-aware query variants to the existing NAS media Skill while retaining QAS preview/execution and all current safety rules.

**Architecture:** A new bounded `PanSouClient` projects Quark results into the existing candidate shape. `MediaService` performs local-first lookup, searches QAS and PanSou across ordered query variants, deduplicates by normalized share URL, and sends every retained candidate through the existing QAS preview/specification pipeline.

**Tech Stack:** Python 3.11 standard library, `unittest`, OpenClaw Agent Skills, PanSou HTTP API, existing QAS client.

## Global Constraints

- Search and preview must never save, download, delete, organize, or modify media.
- QAS remains the only preview and execution backend.
- PanSou discovers only Quark results from `merged_by_type.quark`.
- Never expose endpoints, share URLs, tokens, cookies, headers, raw responses, or stack traces.
- Preserve NAS-local-first behavior and protected media-library rules.
- Preserve user choice; never auto-select or auto-download a candidate.
- Admit at most 50 unique PanSou candidates by default and at most 100 when configured.

---

### Task 1: Query Variants and PanSou Client

**Files:**
- Create: `scripts/pansou_client.py`
- Create: `tests/test_pansou_client.py`

**Interfaces:**
- Produces: `query_variants(query: str) -> list[str]`
- Produces: `PanSouClient(base_url, opener=urllib.request.urlopen, timeout=30, max_candidates=50)`
- Produces: `PanSouClient.search(query: str) -> list[dict]`
- Produces: `PanSouError`

- [ ] **Step 1: Write failing tests**

Test exact ordered variants for `幼女战记2`, no expansion for `2046` or `86`,
Quark result projection, URL-safe `repr`, malformed JSON, bounded errors, and
post-dedup candidate limiting.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m unittest tests.test_pansou_client -v
```

Expected: import failure because `pansou_client` does not exist.

- [ ] **Step 3: Implement the minimal client**

Use `urllib.parse.urlencode` with:

```python
{
    "kw": query,
    "cloud_types": "quark",
    "res": "all",
    "src": "all",
}
```

Project each valid item to:

```python
{
    "taskname": str(item.get("note") or "PanSou candidate"),
    "shareurl": normalized_url,
    "discoverySource": "pansou",
    "datetime": str(item.get("datetime") or ""),
}
```

Only accept `https://pan.quark.cn/s/` URLs. Deduplicate without exposing URL
values in exceptions.

- [ ] **Step 4: Verify GREEN**

Run the focused test and expect all tests to pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/pansou_client.py tests/test_pansou_client.py
git commit -m "feat: add bounded PanSou discovery client"
```

### Task 2: Aggregate QAS and PanSou Discovery

**Files:**
- Modify: `scripts/media_service.py`
- Modify: `tests/test_media_service.py`

**Interfaces:**
- Consumes: `query_variants`, `PanSouClient.search`
- Changes: `MediaService(catalog, qas, store, pansou=None)`
- Produces: safe `warnings` and `discoverySources` output fields

- [ ] **Step 1: Write failing aggregation tests**

Add tests proving:

- local hit calls neither remote source;
- `幼女战记2` queries both sources with all four variants;
- duplicate QAS/PanSou share URLs preview once;
- PanSou-only candidates are previewed through QAS;
- PanSou failure retains QAS candidates and adds `pansou_unavailable`;
- serialized output contains no share URL or endpoint;
- update search uses the same discovery aggregation.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m unittest tests.test_media_service -v
```

Expected: failures because `MediaService` has no PanSou dependency and performs
only one QAS query.

- [ ] **Step 3: Implement ordered discovery**

Add one private iterator that:

1. gets ordered variants;
2. appends QAS then PanSou candidates per variant;
3. normalizes and deduplicates Quark share URLs;
4. merges `discoverySources` when the same URL appears again;
5. catches only `PanSouError` and records `pansou_unavailable`.

Reuse that iterator in normal and update searches. Do not duplicate preview,
archive filtering, episode-difference, grouping, or state-store logic.

- [ ] **Step 4: Verify GREEN and regressions**

Run:

```powershell
python -m unittest tests.test_media_service tests.test_episode_diff tests.test_output_contract -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/media_service.py tests/test_media_service.py
git commit -m "feat: combine QAS and PanSou media discovery"
```

### Task 3: Runtime Configuration and Skill Contract

**Files:**
- Modify: `scripts/resource_agent.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_skill_contract.py`
- Modify: `SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `PANSOU_BASE_URL`, `PANSOU_MAX_CANDIDATES`
- Produces: `MediaService(..., pansou=PanSouClient(...))`

- [ ] **Step 1: Write failing contract tests**

Require:

- `PANSOU_BASE_URL` in Skill required environment metadata;
- `PANSOU_MAX_CANDIDATES` in Skill environment metadata;
- runtime loader creates a PanSou client;
- default max is 50;
- invalid, zero, negative, and greater-than-100 values normalize safely;
- README describes local → QAS/PanSou → QAS preview flow.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m unittest tests.test_cli tests.test_skill_contract tests.test_readme_contract -v
```

Expected: failures for missing PanSou configuration and documentation.

- [ ] **Step 3: Implement runtime wiring**

Parse the candidate limit with:

```python
def _pansou_limit(value: str | None) -> int:
    try:
        parsed = int(value or "50")
    except ValueError:
        return 50
    return parsed if 1 <= parsed <= 100 else 50
```

Instantiate `PanSouClient` from private environment values and pass it through
the service factory. Update `SKILL.md` metadata and search instructions without
adding a real NAS address.

- [ ] **Step 4: Verify GREEN**

Run the focused contract tests and expect all to pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/resource_agent.py tests/test_cli.py tests/test_skill_contract.py SKILL.md README.md
git commit -m "feat: configure PanSou discovery for OpenClaw"
```

### Task 4: Full Local Verification

**Files:**
- Verify only

**Interfaces:**
- Consumes: completed local implementation
- Produces: clean full-suite evidence

- [ ] **Step 1: Run all tests**

```powershell
python -m unittest discover -s tests -v
```

Expected: all non-platform-skipped tests pass.

- [ ] **Step 2: Run static checks**

```powershell
python -m py_compile scripts/pansou_client.py scripts/media_service.py scripts/resource_agent.py
git diff --check
git status --short
```

Expected: no syntax or whitespace errors; only intentional commits.

### Task 5: Safe NAS Deployment and Live Regression

**Files:**
- Back up: `/volume4/openclaw/skills/resource-download-agent`
- Back up: `/volume4/docker/openclaw/config/openclaw.json`
- Modify: `/volume4/docker/openclaw/config/openclaw.json`
- Replace tested Skill files under:
  `/volume4/openclaw/skills/resource-download-agent`

**Interfaces:**
- Consumes: tested repository commit and existing private OpenClaw config
- Produces: OpenClaw search candidates for `幼女战记2` with no download

- [ ] **Step 1: Create timestamped protected backups**

Copy the installed Skill and OpenClaw JSON to a mode-`0700` backup directory
under `/volume4/openclaw/backups/`.

- [ ] **Step 2: Add private configuration**

Set:

```text
PANSOU_BASE_URL=the private LAN endpoint of the existing PanSou service
PANSOU_MAX_CANDIDATES=50
```

Modify JSON in memory and atomically replace the config with mode `0600`.
Never print the endpoint or any sibling secret.

- [ ] **Step 3: Synchronize only tested files**

Copy the committed Skill tree to the installed Skill directory without touching
media directories. Preserve executable mode on `bin/mediactl`.

- [ ] **Step 4: Run tests inside OpenClaw**

```sh
docker exec comugreendockeropenclaw-gateway-1 \
  python3 -m unittest discover \
  -s /root/.openclaw/workspace/skills/resource-download-agent/tests -v
```

Expected: all non-platform-skipped tests pass.

- [ ] **Step 5: Restart and verify gateway**

Restart only `comugreendockeropenclaw-gateway-1`, wait for healthy, and require
restart count to remain stable after initialization.

- [ ] **Step 6: Run preview-only regression**

Invoke the fixed `mediactl search` for `幼女战记2` with `--media-type anime`.
Require:

- `candidateCount > 0`;
- at least one candidate discovered through PanSou;
- multiple query variants were used by tests;
- no task, plan, download, or media file was created;
- output contains no share URL, endpoint, token, or raw response.

- [ ] **Step 7: Roll back on failure**

If tests, health, or live search fail, restore both the Skill and OpenClaw JSON,
restart only the gateway, and verify its previous healthy state.

### Task 6: Publish

**Files:**
- Verify repository history and clean state

**Interfaces:**
- Produces: reviewed commits on GitHub

- [ ] **Step 1: Re-run the full suite**

Expected: all tests pass immediately before publication.

- [ ] **Step 2: Push the tested branch**

Use the repository's configured GitHub credentials. Do not rewrite history or
force-push.

- [ ] **Step 3: Verify remote commit**

Confirm the remote branch resolves to the exact locally tested commit.
