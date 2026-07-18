# Safe Media Agent Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a NAS-first OpenClaw media Skill whose preview paths are provably read-only, whose update paths download only missing episodes, and whose only executable capability is a fixed `mediactl` program.

**Architecture:** Put all fragile behavior behind a low-freedom `mediactl` executable. `MediaService` coordinates a bounded local catalog, a read-only QAS facade, an immutable plan store, and existing aria2 task ownership checks. OpenClaw receives only versioned, allowlisted JSON and is configured to deny every executable except `mediactl` without prompting.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, OpenClaw Agent Skills, OpenClaw exec allowlist, QAS HTTP API, aria2 JSON-RPC.

## Global Constraints

- User data access is limited to `/volume2/downloads`, `/volume2/影视`, `/volume3/临时影视`, and `/volume4/openclaw`.
- Every QAS/aria2 download target is `/volume2/downloads/.incoming/<task-id>`; formal media roots are never download destinations.
- Completed downloads move through `.ready` or `.quarantine`; transfer to a formal library requires a separate organize plan.
- NAS library results always appear before remote candidates.
- A local title match terminates a normal search without remote calls.
- Update and gap-fill operations may plan only episodes absent from the library, active downloads, and unexpired plans.
- `library`, `search`, and `preview` commands must make zero QAS write calls, zero aria2 mutations, and zero media filesystem writes.
- Secrets must never appear in command arguments, JSON output, exceptions, logs, or Agent context.
- All mutations use immutable, expiring, single-use plans; delete, overwrite, cleanup, organize, and transcode require separate confirmation.
- OpenClaw uses `tools.exec.ask=off` and `tools.exec.security=allowlist`; only the absolute `mediactl` executable is allowlisted.
- Non-allowlisted commands are denied without an `/approve` prompt.
- Every production behavior is implemented test-first and every test must be observed failing for the intended reason before production code is changed.

---

## File Structure

**Create**

- `scripts/output_contract.py` — versioned JSON envelope and recursive secret-safe projection.
- `scripts/library_catalog.py` — normalized title lookup and episode inventory.
- `scripts/media_service.py` — NAS-first decision engine and read-only preview orchestration.
- `scripts/episode_diff.py` — episode identity extraction and incremental set calculation.
- `scripts/organizer.py` — completion validation and crash-safe transfer from the download area into a formal library.
- `scripts/session_sanitizer.py` — backup-safe historical OpenClaw JSONL redaction.
- `bin/mediactl` — sole executable entrypoint exposed to OpenClaw.
- `tests/test_output_contract.py`
- `tests/test_library_catalog.py`
- `tests/test_media_service.py`
- `tests/test_episode_diff.py`
- `tests/test_organizer.py`
- `tests/test_mediactl.py`
- `tests/test_session_sanitizer.py`

**Modify**

- `scripts/qas_client.py` — separate explicitly named read and write capabilities and project responses.
- `scripts/planner.py` — consume preview selections and incremental episode evidence instead of performing implicit search.
- `scripts/state_store.py` — store plan hashes/schema and expose pending episode identities.
- `scripts/resource_agent.py` — retain task control internals but route public commands through the safe service.
- `config/routing.json` — route every media type through the unified download root while retaining formal destinations.
- `SKILL.md` — concise trigger-rich, low-freedom OpenClaw instructions using only `mediactl`.
- `tests/test_clients.py`
- `tests/test_planner.py`
- `tests/test_state_store.py`
- `tests/test_cli.py`
- `tests/test_skill_contract.py`

---

### Task 1: Versioned Safe Output Contract

**Files:**
- Create: `scripts/output_contract.py`
- Create: `tests/test_output_contract.py`
- Modify: `scripts/resource_agent.py`

**Interfaces:**
- Produces: `success(data, *, terminal=False, next_action="none") -> dict`
- Produces: `failure(code, message, *, next_action) -> dict`
- Produces: `safe_project(value) -> JSONValue`
- Consumes: plain Python mappings and sequences from service methods.

- [ ] **Step 1: Write the failing tests**

```python
import json
import unittest

from output_contract import failure, safe_project, success

class OutputContractTests(unittest.TestCase):
    def test_success_has_stable_schema(self):
        result = success(
            {"local": []},
            terminal=True,
            next_action="stop_local_exists",
        )
        self.assertEqual(result, {
            "schemaVersion": 1,
            "ok": True,
            "terminal": True,
            "nextAction": "stop_local_exists",
            "data": {"local": []},
            "error": None,
        })

    def test_projection_removes_secrets_recursively_and_from_json_strings(self):
        source = {
            "title": "Example",
            "cookie": "danger",
            "nested": {"Authorization": "Bearer danger"},
            "raw": '{"token":"danger","file_name":"E01.mkv"}',
        }
        serialized = json.dumps(safe_project(source), ensure_ascii=False).lower()
        self.assertNotIn("danger", serialized)
        self.assertNotIn("cookie", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertIn("e01.mkv", serialized)

    def test_failure_never_returns_tracebacks(self):
        result = failure("QAS_TIMEOUT", "request failed", next_action="retry_later")
        self.assertEqual(
            result["error"],
            {"code": "QAS_TIMEOUT", "message": "request failed"},
        )
        self.assertNotIn("traceback", json.dumps(result).lower())
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_output_contract -v
```

Expected: import failure because `output_contract` does not exist.

- [ ] **Step 3: Implement the minimal output contract**

Implement a recursive projector with a fixed forbidden-key set:

```python
FORBIDDEN_KEYS = {
    "authorization", "cookie", "set-cookie", "token", "qas_token",
    "aria2_rpc_secret", "headers", "request", "response", "environment",
}

def safe_project(value):
    if isinstance(value, dict):
        return {
            str(key): safe_project(item)
            for key, item in value.items()
            if str(key).casefold() not in FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [safe_project(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return value
        return safe_project(parsed)
    return value
```

Build `success` and `failure` exclusively from projected values. Replace the ad-hoc envelope in `resource_agent.main` with these functions.

- [ ] **Step 4: Run focused and existing CLI tests and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_output_contract tests.test_cli -v
```

Expected: all tests pass and emitted JSON contains `schemaVersion`.

- [ ] **Step 5: Commit**

```powershell
git add scripts/output_contract.py scripts/resource_agent.py tests/test_output_contract.py tests/test_cli.py
git commit -m "feat: add safe versioned agent output"
```

### Task 2: Bounded NAS Library Catalog

**Files:**
- Create: `scripts/library_catalog.py`
- Create: `tests/test_library_catalog.py`
- Modify: `scripts/library_scanner.py`

**Interfaces:**
- Produces: `MediaIdentity(title_key: str, season: int | None, episode: int | None, special: str | None)`
- Produces: `LibraryCatalog(roots: Mapping[str, Path]).lookup(query: str, media_type: str | None) -> dict`
- Consumes: `library_scanner.scan(root)` entries.

- [ ] **Step 1: Write the failing tests**

```python
import tempfile
import unittest
from pathlib import Path

from library_catalog import LibraryCatalog


class LibraryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "Anime"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_lookup_finds_normalized_title_and_lists_episodes(self):
        season = self.root / "凡人修仙传 (2020)" / "Season 01"
        season.mkdir(parents=True)
        (season / "凡人修仙传 (2020) - S01E001.mkv").write_bytes(b"x")
        (season / "凡人修仙传 (2020) - S01E002.mkv").write_bytes(b"x")
        catalog = LibraryCatalog({"anime": self.root})

        result = catalog.lookup("搜索《凡人修仙传》动画资源", "anime")

        self.assertTrue(result["found"])
        self.assertEqual(result["title"], "凡人修仙传")
        self.assertEqual(
            result["episodes"],
            [{"season": 1, "episode": 1}, {"season": 1, "episode": 2}],
        )
        self.assertEqual(result["fileCount"], 2)

    def test_lookup_does_not_match_similar_but_different_title(self):
        (self.root / "凡人修仙记").mkdir()
        catalog = LibraryCatalog({"anime": self.root})
        self.assertFalse(catalog.lookup("凡人修仙传", "anime")["found"])

    def test_catalog_skips_incoming_and_symlink_escape(self):
        incoming = self.root / ".incoming" / "凡人修仙传"
        incoming.mkdir(parents=True)
        (incoming / "凡人修仙传.S01E001.mkv").write_bytes(b"x")
        outside = self.base / "outside" / "凡人修仙传"
        outside.mkdir(parents=True)
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")

        result = LibraryCatalog({"anime": self.root}).lookup(
            "凡人修仙传",
            "anime",
        )

        self.assertFalse(result["found"])
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_library_catalog -v
```

Expected: import failure because `library_catalog` does not exist.

- [ ] **Step 3: Implement normalized directory-first lookup**

Implement:

```python
TITLE_WRAPPERS = re.compile(r"[《》「」『』【】]")
INTENT_WORDS = re.compile(r"(?:搜索|查找|看看|预览|下载|资源|动画|动漫|电视剧|电影)")

def title_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = TITLE_WRAPPERS.sub("", text)
    text = INTENT_WORDS.sub("", text)
    text = re.sub(r"\((?:19|20)\d{2}\)", "", text)
    return re.sub(r"[\W_]+", "", text)
```

Index only direct work directories under configured category roots, then scan only matched work directories for episode details. Bound a lookup to 20 candidate directories and 10,000 media entries. Return paths only inside configured roots.

- [ ] **Step 4: Run catalog, scanner, and path tests and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_library_catalog tests.test_library_scanner tests.test_path_guard -v
```

Expected: all tests pass; `.incoming` and symlink escapes are absent.

- [ ] **Step 5: Commit**

```powershell
git add scripts/library_catalog.py scripts/library_scanner.py tests/test_library_catalog.py
git commit -m "feat: add bounded NAS media catalog"
```

### Task 3: NAS-First Read-Only Media Service

**Files:**
- Create: `scripts/media_service.py`
- Create: `tests/test_media_service.py`
- Modify: `scripts/qas_client.py`
- Modify: `scripts/state_store.py`
- Modify: `tests/test_clients.py`
- Modify: `tests/test_state_store.py`

**Interfaces:**
- Produces: `MediaService.lookup(query, media_type=None) -> dict`
- Produces: `MediaService.search(query, media_type=None, update=False) -> dict`
- Produces: `MediaService.preview(candidate_id) -> dict`
- Produces: `StateStore.create_candidate(payload, ttl_seconds=900) -> str`
- Produces: `StateStore.get_candidate(candidate_id) -> dict`
- Consumes: `LibraryCatalog`, `QasReader`, and no QAS writer.

- [ ] **Step 1: Write tests that prove local termination and zero writes**

```python
import unittest


class RecordingQas:
    def __init__(self):
        self.reads = []
        self.writes = []
    def search(self, query, deep=True):
        self.reads.append(("search", query))
        return []
    def get_share(self, url, show_all=True):
        self.reads.append(("share", url))
        return {"share": {}, "list": []}
    def add_task(self, task):
        self.writes.append(("add", task))
    def run_task(self, task):
        self.writes.append(("run", task))


class FakeCatalog:
    def __init__(self, found):
        self.found = found

    def lookup(self, query, media_type):
        return {
            "found": self.found,
            "title": "凡人修仙传" if self.found else "",
            "episodes": [{"season": 1, "episode": 1}] if self.found else [],
            "fileCount": 1 if self.found else 0,
        }


class MediaServiceTests(unittest.TestCase):
    def test_normal_search_stops_on_local_match(self):
        qas = RecordingQas()
        result = MediaService(FakeCatalog(True), qas).search(
            "搜索《凡人修仙传》动画资源",
            media_type="anime",
        )
        self.assertTrue(result["terminal"])
        self.assertEqual(result["nextAction"], "stop_local_exists")
        self.assertTrue(result["data"]["local"]["found"])
        self.assertEqual(qas.reads, [])
        self.assertEqual(qas.writes, [])

    def test_remote_search_is_read_only_when_local_missing(self):
        qas = RecordingQas()
        MediaService(FakeCatalog(False), qas).search(
            "沙丘2",
            media_type="movie",
        )
        self.assertEqual(qas.reads, [("search", "沙丘2")])
        self.assertEqual(qas.writes, [])
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_media_service -v
```

Expected: import failure because `media_service` does not exist.

- [ ] **Step 3: Implement local-first orchestration and QAS projections**

Implement the observable branch:

```python
local = self.catalog.lookup(query, media_type)
if local["found"] and not update:
    return success(
        {"local": local, "missing": [], "remoteCandidates": []},
        terminal=True,
        next_action="stop_local_exists",
    )
candidates = self.qas.search(query, deep=True)
return success(
    {"local": local, "missing": [], "remoteCandidates": project_candidates(candidates)},
    terminal=False,
    next_action="choose_candidate",
)
```

`project_candidates` must generate opaque candidate IDs stored in a new expiring `candidates` SQLite table and expose only title, provider, size/episode summary, score, and quality fields. `get_candidate` must reject missing and expired IDs. Agent-visible output must omit raw share URLs.

- [ ] **Step 4: Run service and client tests and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_media_service tests.test_clients tests.test_state_store -v
```

Expected: all tests pass; local match produces no QAS calls.

- [ ] **Step 5: Commit**

```powershell
git add scripts/media_service.py scripts/qas_client.py scripts/state_store.py tests/test_media_service.py tests/test_clients.py tests/test_state_store.py
git commit -m "feat: enforce NAS-first read-only search"
```

### Task 4: Incremental Episode Difference

**Files:**
- Create: `scripts/episode_diff.py`
- Create: `tests/test_episode_diff.py`
- Modify: `scripts/media_service.py`
- Modify: `scripts/state_store.py`
- Modify: `tests/test_state_store.py`

**Interfaces:**
- Produces: `EpisodeKey(title_key: str, season: int, episode: int, special: str | None)`
- Produces: `compute_missing(remote, local, active, planned) -> list[EpisodeKey]`
- Produces: `StateStore.pending_episode_keys(title_key) -> set[EpisodeKey]`

- [ ] **Step 1: Write the failing difference tests**

```python
import unittest

from episode_diff import EpisodeKey, compute_missing, select_incremental_files


def keys(title, season, episodes):
    return {
        EpisodeKey(title_key=title.casefold(), season=season, episode=episode)
        for episode in episodes
    }


class EpisodeDifferenceTests(unittest.TestCase):
    def test_update_returns_only_new_episode(self):
        remote = keys("凡人修仙传", season=1, episodes=[118, 119, 120])
        local = keys("凡人修仙传", season=1, episodes=[1, 118, 119])
        self.assertEqual(
            compute_missing(remote, local, set(), set()),
            keys("凡人修仙传", season=1, episodes=[120]),
        )

    def test_active_and_planned_episodes_are_not_returned(self):
        remote = keys("Show", season=2, episodes=[3, 4])
        self.assertEqual(
            compute_missing(
                remote,
                set(),
                keys("Show", 2, [3]),
                keys("Show", 2, [4]),
            ),
            set(),
        )

    def test_unparseable_collection_is_not_incrementally_selectable(self):
        result = select_incremental_files(
            [{"file_name": "全集文件夹", "dir": True}],
            wanted=keys("Show", 1, [12]),
        )
        self.assertEqual(result, {"selectable": False, "files": []})
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_episode_diff -v
```

Expected: import failure because `episode_diff` does not exist.

- [ ] **Step 3: Implement explicit episode identities and set subtraction**

Parse `S01E120`, `EP120`, `第120集`, `OVA`, and `SP` into immutable keys. Reject ambiguous season/episode extraction instead of guessing. Make update search return:

```python
if not missing:
    return success(
        {"local": local, "missing": [], "remoteCandidates": []},
        terminal=True,
        next_action="already_up_to_date",
    )
if not selection["selectable"]:
    return success(
        {"local": local, "missing": serialize(missing), "remoteCandidates": []},
        terminal=True,
        next_action="incremental_selection_unavailable",
    )
```

- [ ] **Step 4: Run difference, service, and state tests and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_episode_diff tests.test_media_service tests.test_state_store -v
```

Expected: all tests pass; existing, active, and planned episode keys never enter `missing`.

- [ ] **Step 5: Commit**

```powershell
git add scripts/episode_diff.py scripts/media_service.py scripts/state_store.py tests/test_episode_diff.py tests/test_media_service.py tests/test_state_store.py
git commit -m "feat: plan only missing media episodes"
```

### Task 5: Immutable Download Plans and Safe Execution

**Files:**
- Modify: `scripts/state_store.py`
- Modify: `scripts/planner.py`
- Modify: `tests/test_state_store.py`
- Modify: `tests/test_planner.py`

**Interfaces:**
- Produces: `StateStore.create_plan(action, payload, ttl_seconds=1800, schema_version=1) -> str`
- Produces: `StateStore.consume_plan(plan_id, action) -> dict` with hash verification.
- Consumes: opaque candidate selection and incremental file list from `MediaService`.

- [ ] **Step 1: Write failing integrity and no-implicit-search tests**

```python
def test_modified_plan_payload_is_rejected(self):
    plan_id = self.store.create_plan("download", {"title": "Safe"})
    self.store.connection.execute(
        "UPDATE plans SET payload_json = ? WHERE plan_id = ?",
        ('{"title":"Changed"}', plan_id),
    )
    self.store.connection.commit()
    with self.assertRaisesRegex(PlanError, "integrity"):
        self.store.consume_plan(plan_id, "download")

def test_planner_never_searches_when_building_from_preview(self):
    class SearchForbiddenQas(FakeQas):
        def search(self, query, deep=True):
            raise AssertionError("planner must not search")

    selection = {
        "query": "Show S01E120",
        "shareurl": "https://pan.quark.cn/s/show",
        "details": {
            "share": {"title": "Show S01E120"},
            "list": [{"file_name": "Show.S01E120.mkv", "dir": False}],
        },
        "selectedFiles": ["Show.S01E120.mkv"],
        "existingEpisodes": [{"season": 1, "episode": 119}],
        "newEpisodes": [{"season": 1, "episode": 120}],
    }
    planner = self.make_planner(SearchForbiddenQas())
    plan = planner.plan_selected(selection)
    self.assertEqual(plan["incremental"]["newEpisodes"], [
        {"season": 1, "episode": 120},
    ])

def test_incremental_plan_pattern_matches_only_missing_files(self):
    selection = {
        "query": "Show S01E120",
        "shareurl": "https://pan.quark.cn/s/show",
        "details": {
            "share": {"title": "Show"},
            "list": [
                {"file_name": "Show.S01E119.mkv", "dir": False},
                {"file_name": "Show.S01E120.mkv", "dir": False},
            ],
        },
        "selectedFiles": ["Show.S01E120.mkv"],
        "existingEpisodes": [{"season": 1, "episode": 119}],
        "newEpisodes": [{"season": 1, "episode": 120}],
    }
    plan = self.make_planner(FakeQas()).plan_selected(selection)
    self.assertRegex("Show.S01E120.mkv", plan["task"]["pattern"])
    self.assertNotRegex("Show.S01E119.mkv", plan["task"]["pattern"])
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_state_store tests.test_planner -v
```

Expected: integrity and `plan_selected` tests fail because fields/methods are absent.

- [ ] **Step 3: Add canonical hashing and selected-only planning**

Canonicalize plan JSON with sorted keys and compact separators, store SHA-256 with schema version, and compare using `hmac.compare_digest` inside the existing immediate transaction. Move candidate search out of `DownloadPlanner`; accept only a stored preview selection ID. Build an anchored escaped regex from selected missing filenames. If no exact filenames can be selected, raise `incremental_selection_unavailable`.

Project QAS execution results to:

```python
return {
    "taskId": plan["taskId"],
    "status": "submitted",
    "action": plan["action"],
}
```

Never return raw QAS events.

- [ ] **Step 4: Run planner and store tests and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_state_store tests.test_planner -v
```

Expected: all tests pass; tampering, re-use, expiration, and action mismatch are rejected.

- [ ] **Step 5: Commit**

```powershell
git add scripts/state_store.py scripts/planner.py tests/test_state_store.py tests/test_planner.py
git commit -m "feat: harden immutable download plans"
```

### Task 6: Strict `mediactl` Executable

**Files:**
- Create: `bin/mediactl`
- Create: `tests/test_mediactl.py`
- Modify: `scripts/resource_agent.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces commands listed in the design: `check-ready`, `library lookup`, `search`, `preview`, `plan download`, `execute`, and download controls.
- Produces: `parse_args(argv) -> argparse.Namespace` with bounded `CliUsageError`.
- Produces: `emit(envelope, stream=sys.stdout) -> None`.
- Consumes secrets only through process environment, never arguments.

- [ ] **Step 1: Write failing CLI contract tests**

```python
import io
import json
import unittest
from pathlib import Path

from resource_agent import CliUsageError, emit, parse_args


class MediaCtlContractTests(unittest.TestCase):
    def test_executable_exists_at_fixed_repository_path(self):
        executable = Path(__file__).resolve().parents[1] / "bin" / "mediactl"
        self.assertTrue(executable.is_file())
        self.assertTrue(executable.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3"))

    def test_output_is_exactly_one_json_document(self):
        stream = io.StringIO()
        emit({
            "schemaVersion": 1,
            "ok": True,
            "terminal": True,
            "nextAction": "stop_local_exists",
            "data": {"local": {"found": True}},
            "error": None,
        }, stream=stream)
        result = json.loads(stream.getvalue())
        self.assertEqual(result["nextAction"], "stop_local_exists")
        self.assertEqual(stream.getvalue().count("\n"), 1)

    def test_unknown_command_is_bounded_error(self):
        with self.assertRaisesRegex(CliUsageError, "invalid command"):
            parse_args(["shell", "curl", "http://example"])

    def test_secret_arguments_are_not_supported_or_echoed(self):
        with self.assertRaises(CliUsageError) as raised:
            parse_args(["search", "x", "--token", "danger"])
        self.assertNotIn("danger", str(raised.exception))
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_mediactl -v
```

Expected: failure because `bin/mediactl` does not exist.

- [ ] **Step 3: Implement the executable and strict parser**

Use a Python shebang and insert only the repository `scripts` directory into `sys.path`. Set `argument_default=argparse.SUPPRESS`, define every valid subcommand explicitly, reject unknown arguments, suppress tracebacks, and print exactly one safe envelope. Do not provide a passthrough or generic request command.

Make `execute` accept only:

```text
mediactl execute PLAN_ID
mediactl execute PLAN_ID --confirmed
```

No destination, URL, cookie, token, shell, Python expression, or raw API path is accepted by execution commands.

- [ ] **Step 4: Run CLI and full local suite and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
```

Expected: all tests pass, with only the existing Windows symlink test allowed to skip.

- [ ] **Step 5: Commit**

```powershell
git add bin/mediactl scripts/resource_agent.py tests/test_mediactl.py tests/test_cli.py
git commit -m "feat: expose strict mediactl capability"
```

### Task 7: Unified Download Area and Crash-Safe Organization

**Files:**
- Create: `scripts/organizer.py`
- Create: `tests/test_organizer.py`
- Modify: `config/routing.json`
- Modify: `scripts/path_guard.py`
- Modify: `scripts/planner.py`
- Modify: `scripts/state_store.py`
- Modify: `scripts/resource_agent.py`

**Interfaces:**
- Produces: `DownloadValidator.validate(task_id) -> ValidationReport`
- Produces: `Organizer.plan(task_id) -> dict`
- Produces: `Organizer.execute(plan_id, confirmed=False) -> dict`
- Consumes only completed managed tasks rooted at `/volume2/downloads/.incoming/<task-id>` or `.ready/<task-id>`.

- [ ] **Step 1: Write failing routing and lifecycle tests**

```python
class OrganizerTests(unittest.TestCase):
    def test_every_route_uses_unified_download_root(self):
        for name, route in self.routing.items():
            if name == "downloads":
                continue
            self.assertEqual(route["staging_root"], "/volume2/downloads/.incoming")
            self.assertEqual(route["aria2_prefix"], "downloads/.incoming")

    def test_incomplete_task_cannot_be_organized(self):
        task = self.make_task(status="active")
        with self.assertRaisesRegex(OrganizeError, "not complete"):
            self.organizer.plan(task["task_id"])

    def test_temporary_or_zero_byte_media_is_quarantined(self):
        task = self.make_task(status="complete")
        source = Path(task["staging_path"])
        source.mkdir(parents=True)
        (source / "episode.mkv").write_bytes(b"")
        (source / "episode.mkv.aria2").write_text("", encoding="utf-8")
        report = self.validator.validate(task["task_id"])
        self.assertEqual(report.next_action, "quarantine_download")

    def test_cross_volume_copy_keeps_source_until_verified(self):
        task = self.make_completed_movie_task()
        plan = self.organizer.plan(task["task_id"])
        self.copy_adapter.fail_verification = True
        with self.assertRaisesRegex(OrganizeError, "verification"):
            self.organizer.execute(plan["planId"], confirmed=True)
        self.assertTrue(Path(task["staging_path"]).exists())
        self.assertFalse(Path(task["final_path"]).exists())

    def test_existing_final_target_is_never_overwritten(self):
        task = self.make_completed_task()
        Path(task["final_path"]).mkdir(parents=True)
        with self.assertRaisesRegex(OrganizeError, "target exists"):
            self.organizer.plan(task["task_id"])
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_organizer -v
```

Expected: import failure because `organizer` does not exist and routing still points at media roots.

- [ ] **Step 3: Implement the state machine and routing**

Set every media route to:

```json
{
  "aria2_prefix": "downloads/.incoming",
  "staging_root": "/volume2/downloads/.incoming"
}
```

Retain each media type's existing `final_root`. Add a top-level `downloads` route with `.incoming`, `.ready`, and `.quarantine`.

Validation must require a managed completed task, no active/waiting/paused GID, no `.aria2`/`.part`/`.tmp`, non-zero allowed media files, a readable `ffprobe` result when available, and an absent final target.

For `/volume2/影视`, rename the verified `.ready/<task-id>` directory on the same filesystem. For `/volume3/临时影视`, copy to `<final-parent>/.organizing-<task-id>`, fsync files and directories, compare the planned manifest, rename to the final target, then remove the `.ready` source. On failure, preserve the source and remove only the owned hidden temporary target after resolving it through `PathGuard`.

- [ ] **Step 4: Expose separate validation and organization commands**

Add:

```text
mediactl downloads validate TASK_ID
mediactl organize plan TASK_ID
mediactl organize execute PLAN_ID --confirmed
```

`downloads validate` may move a failed task only to `/volume2/downloads/.quarantine/<task-id>`. `organize execute` accepts no source or destination path from the Agent.

- [ ] **Step 5: Run organizer, planner, path, and CLI tests and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_organizer tests.test_planner tests.test_path_guard tests.test_cli tests.test_mediactl -v
```

Expected: all tests pass; no QAS/aria2 plan targets a formal media root.

- [ ] **Step 6: Commit**

```powershell
git add config/routing.json scripts/organizer.py scripts/path_guard.py scripts/planner.py scripts/state_store.py scripts/resource_agent.py tests/test_organizer.py tests/test_planner.py tests/test_path_guard.py tests/test_cli.py tests/test_mediactl.py
git commit -m "feat: stage downloads before safe library transfer"
```

### Task 8: Redact Historical OpenClaw Session Leakage

**Files:**
- Create: `scripts/session_sanitizer.py`
- Create: `tests/test_session_sanitizer.py`

**Interfaces:**
- Produces: `sanitize_jsonl(source: Path, destination: Path) -> SanitizeReport`
- Consumes: OpenClaw JSONL one line at a time.

- [ ] **Step 1: Write failing sanitizer tests**

```python
import json
import tempfile
import unittest
from pathlib import Path

from session_sanitizer import sanitize_jsonl


class SessionSanitizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_sanitizer_redacts_nested_and_stringified_secrets(self):
        source = self.root / "session.jsonl"
        source.write_text(
            json.dumps({
                "type": "toolResult",
                "content": '{"cookie":"secret-cookie","title":"凡人修仙传"}',
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        destination = self.root / "sanitized.jsonl"
        report = sanitize_jsonl(source, destination)
        text = destination.read_text(encoding="utf-8")
        self.assertNotIn("secret-cookie", text)
        self.assertIn("凡人修仙传", text)
        self.assertEqual(report.redacted_records, 1)

    def test_malformed_line_is_replaced_not_copied(self):
        source = self.root / "session.jsonl"
        source.write_text("not-json cookie=secret-cookie\n", encoding="utf-8")
        destination = self.root / "sanitized.jsonl"
        sanitize_jsonl(source, destination)
        text = destination.read_text(encoding="utf-8")
        self.assertNotIn("secret-cookie", text)
        self.assertIn("securityRedaction", text)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_session_sanitizer -v
```

Expected: import failure because `session_sanitizer` does not exist.

- [ ] **Step 3: Implement streaming backup-safe sanitization**

Parse each line independently. Recursively redact forbidden keys and parse stringified JSON when possible. For malformed lines matching case-insensitive credential markers, emit:

```json
{"type":"securityRedaction","content":"[REDACTED SENSITIVE TOOL RESULT]"}
```

Never modify a source file in place. Deployment must write a new file, fsync it, preserve mode, move the original to the dated backup directory, then atomically replace it.

- [ ] **Step 4: Run sanitizer and output tests and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_session_sanitizer tests.test_output_contract -v
```

Expected: all tests pass and fixture secrets are absent from sanitized output.

- [ ] **Step 5: Commit**

```powershell
git add scripts/session_sanitizer.py tests/test_session_sanitizer.py
git commit -m "fix: sanitize sensitive OpenClaw session output"
```

### Task 9: Rewrite the Agent Skill as a Low-Freedom Contract

**Files:**
- Modify: `SKILL.md`
- Modify: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes only the fixed absolute `mediactl` path.
- Produces a concise Chinese local-first status report.

- [ ] **Step 1: Strengthen the contract test and verify the old Skill fails**

```python
def test_skill_is_trigger_rich_and_exposes_only_mediactl():
    content = skill_path.read_text(encoding="utf-8")
    frontmatter = content.split("---", 2)[1]
    for trigger in ("搜索", "预览", "影视", "动画", "追更", "补集", "暂停", "转码"):
        assert trigger in frontmatter
    assert "mediactl" in content
    for forbidden in ("python3 ", "curl ", "/run_script_now", "QAS_TOKEN"):
        assert forbidden not in content

def test_skill_requires_terminal_stop_and_local_first_output():
    assert "terminal" in content
    assert "stop_local_exists" in content
    assert content.index("NAS 本地") < content.index("远端候选")
```

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_skill_contract -v
```

Expected: FAIL because the existing Skill exposes `python3` and secret environment names and lacks the terminal contract.

- [ ] **Step 2: Rewrite `SKILL.md`**

Use frontmatter with only `name` and a trigger-only `description`. Keep the body under 300 lines. Define this positive recipe:

1. Call `mediactl search` for title/resource queries.
2. If `terminal` is true, report `data.local` first and stop all tool calls.
3. For updates, report local inventory, missing episodes, then remote candidates.
4. Call `preview` before any plan.
5. Call `plan download`; execute only a returned plan ID.
6. Never improvise a replacement command when `mediactl` fails.

Include a red-flags table for the observed violations: raw Python, direct QAS calls, preview writes, generic web fallback after terminal local match, broad directory listing, and secret output.

- [ ] **Step 3: Validate the Skill and verify GREEN**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest tests.test_skill_contract -v
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "C:\Users\Administrator\.codex\skills\.system\skill-creator\scripts\quick_validate.py" .
```

Expected: contract tests pass and validator reports a valid Skill.

- [ ] **Step 4: Run the complete local suite**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
```

Expected: all tests pass; no unexpected warnings or errors.

- [ ] **Step 5: Commit**

```powershell
git add SKILL.md tests/test_skill_contract.py
git commit -m "feat: make media skill local-first and low-freedom"
```

### Task 10: Deploy with Exec Allowlist and Sanitize the Incident

**Files:**
- Deploy repository files to: `/volume4/openclaw/skills/resource-download-agent`
- Deploy executable to: `/volume4/openclaw/bin/mediactl`
- Backup OpenClaw config/session data under: `/volume4/openclaw/backups/<timestamp>`

**Interfaces:**
- Consumes the tested repository artifact.
- Produces a running OpenClaw Skill snapshot and hardened exec policy.

- [ ] **Step 1: Capture read-only pre-deployment evidence**

Record without printing values:

```text
current Skill checksum
current exec ask/security values
current aria2 active/waiting counts
current `/volume2/downloads/.incoming` file counts
presence of the known incident session file
presence of the mistaken cloud preview folder
```

Expected: evidence contains counts and configured/not-configured booleans only.

- [ ] **Step 2: Back up live files**

Create a dated directory under `/volume4/openclaw/backups`. Copy the current Skill, OpenClaw config, exec policy, state DB, and affected session JSONL. Resolve and verify every backup source and destination stays below `/volume4/openclaw` before copying.

- [ ] **Step 3: Sanitize the affected session**

Run the tested sanitizer to a new file, verify:

```text
line count is preserved or explicitly reported
JSONL parsing succeeds
credential marker count is zero in Agent-visible tool results
original is retained in the protected backup
```

Do not print the matched secret. Recommend Cookie rotation to the user; do not clear the configured Cookie automatically.

- [ ] **Step 4: Deploy artifact and switch policy in safe order**

1. Install and `chmod 0755` `/volume4/openclaw/bin/mediactl`.
2. Add `/volume2/downloads:/volume2/downloads` to the OpenClaw gateway compose.
3. Add `/volume2/downloads:/nas/downloads` to the aria2 container.
4. Create `.incoming`, `.ready`, and `.quarantine` with ownership and mode limited to the media workflow.
5. Deploy the Skill and Python modules.
6. Set `tools.exec.ask=off`.
7. Set `tools.exec.security=allowlist`.
8. Add only `/volume4/openclaw/bin/mediactl` to the allowlist.
9. Remove broad interpreter/shell entries from the applicable OpenClaw exec policy.
10. Recreate only containers whose volume definitions changed, then verify their health and mounts.

At no point after deployment begins may the live policy return to `security=full`.

- [ ] **Step 5: Verify policy without side effects**

From OpenClaw:

```text
mediactl check-ready                      → allowed, no approval
mediactl library lookup 凡人修仙传        → allowed, no approval
python3 -c "print(1)"                     → denied, no approval
curl http://127.0.0.1                     → denied, no approval
ls /volume1                               → denied, no approval
```

Expected: only `mediactl` runs; all generic commands fail directly and never produce `/approve`.

- [ ] **Step 6: Commit any environment-derived deployment metadata**

Commit only non-secret path/schema adjustments required by the verified NAS environment. Do not commit credentials, host-specific session IDs, raw logs, or backups.

### Task 11: OpenClaw Conversation and Short Download Lifecycle Acceptance

**Files:**
- No production files unless a failing acceptance case first receives a regression test.

**Interfaces:**
- Consumes the deployed OpenClaw Skill and live NAS services.
- Produces acceptance evidence with before/after side-effect counts.

- [ ] **Step 1: Run the mandatory local-first prompt**

Prompt:

```text
搜索《凡人修仙传》动画资源，先预览，不要下载
```

Before and after, capture aria2 active/waiting counts, `/volume2/downloads/.incoming` counts, and QAS task/cloud-folder counts without exposing secrets.

Expected:

- Skill triggers.
- NAS result is first.
- `nextAction=stop_local_exists`.
- No remote search, generic web call, QAS write, aria2 mutation, directory creation, or approval prompt.
- Response finishes within the configured command timeout.

- [ ] **Step 2: Run incremental-update read-only acceptance**

Prompt:

```text
检查《凡人修仙传》有没有新集，只预览
```

Expected: local episode inventory first; only missing episodes appear in candidates. If selection cannot be bounded to missing files, result is `incremental_selection_unavailable` and no write occurs.

- [ ] **Step 3: Run command denial acceptance**

Ask OpenClaw to inspect NAS using arbitrary shell/Python instead of the Skill.

Expected: direct denial without `/approve`; no command executes.

- [ ] **Step 4: Run one confirmed short download lifecycle**

Use a small, unambiguous, non-duplicate media candidate:

1. Search and preview.
2. Generate plan and report side effects.
3. Confirm execution.
4. Verify task appears in `downloads list`.
5. Pause, verify paused.
6. Resume, verify active/waiting.
7. Cancel, verify cancelled.
8. Preserve partial data; do not delete it without a separate confirmed cleanup plan.

Do not wait for the full media file.

- [ ] **Step 5: Verify no collateral changes**

Confirm:

- No files outside the four authorized roots changed.
- No QAS or aria2 task targeted `/volume2/影视` or `/volume3/临时影视`.
- No foreign aria2 GID was controlled.
- No existing episode was planned or downloaded.
- No raw secret appears in new OpenClaw session output.
- The mistaken cloud preview folder was not modified; preview it and request separate deletion confirmation from the user.

- [ ] **Step 6: Run final local verification and commit fixes**

Run:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
git status --short
```

Expected: complete suite passes, working tree is clean, and acceptance evidence satisfies every safety gate in the design.
