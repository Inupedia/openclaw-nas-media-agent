# Existing Media Skill Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing NAS media Skill with `drama`, complete specification choices, strict incremental behavior, and permanent delete protection for both formal libraries.

**Architecture:** Keep the current `mediactl` entrypoint, QAS client, state store, planner, and organizer. Add specification extraction to the existing classifier, make `MediaService` deep-inspect and group existing search results, and add protected-root mutation checks to the existing path guard.

**Tech Stack:** Python 3.11 standard library, `unittest`, SQLite, existing QAS and aria2 clients, Docker Compose.

## Global Constraints

- Do not add another search provider or classification hierarchy.
- `/volume2/影视` and `/volume3/临时影视` are never deletable, overwritable, cleanable, or movable out through OpenClaw.
- New episodic plans contain only locally missing episodes.
- Search may order candidates but must never select one automatically.
- Raw share URLs and credentials remain private.
- All downloads still start under `/volume2/downloads/.incoming`.

---

### Task 1: Add `drama` as a compatible episodic type

**Files:**
- Modify: `config/routing.json`
- Modify: `scripts/resource_agent.py`
- Modify: `scripts/media_classifier.py`
- Test: `tests/test_mediactl.py`
- Test: `tests/test_media_rules.py`

**Interfaces:**
- Consumes: existing `parse_args(argv)` and `classify(query, share)`.
- Produces: accepted media type `drama`; `Classification.media_type == "drama"` for ordinary episodic media.

- [ ] **Step 1: Write failing tests**

```python
def test_search_accepts_drama_media_type(self):
    args = parse_args(["search", "庆余年", "--media-type", "drama"])
    self.assertEqual(args.media_type, "drama")

def test_episode_markers_classify_as_drama(self):
    result = classify(
        "庆余年",
        {"list": [{"file_name": "庆余年.S01E01.mkv"}]},
    )
    self.assertEqual(result.media_type, "drama")
```

Add a routing assertion:

```python
def test_drama_uses_tv_routes(self):
    routing = json.loads(Path("config/routing.json").read_text("utf-8"))
    for key in ("cloud_prefix", "staging_root", "final_root"):
        self.assertEqual(routing["drama"][key], routing["tv"][key])
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m unittest tests.test_mediactl tests.test_media_rules
```

Expected: `drama` is rejected by argparse and the classifier returns `tv`.

- [ ] **Step 3: Implement the minimal compatibility change**

Add `"drama"` to both `--media-type` choice tuples. Add a `drama` route with
the same values as `tv`. Change the episodic fallback:

```python
elif episodes:
    media_type = "drama"
    reasons.append("episode_marker")
```

Keep `tv` untouched in the CLI and routing file.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command again.

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add config/routing.json scripts/resource_agent.py scripts/media_classifier.py tests/test_mediactl.py tests/test_media_rules.py
git commit -m "feat: add drama media type"
```

---

### Task 2: Extract safe candidate specifications

**Files:**
- Modify: `scripts/media_classifier.py`
- Test: `tests/test_media_rules.py`

**Interfaces:**
- Consumes: `extract_candidate_spec(share: dict)`.
- Produces: a JSON-safe dictionary with `resolution`, `dynamicRange`,
  `videoCodec`, `audioFormat`, `subtitleClass`, `subtitleForm`, `totalBytes`,
  `fileCount`, and `episodeCoverage`.

- [ ] **Step 1: Write failing extraction tests**

```python
def test_extracts_quality_size_and_bilingual_external_subtitles(self):
    share = {
        "list": [
            {
                "file_name": "Show.S01E01.2160p.DV.HDR.HEVC.Atmos.mkv",
                "size": 8_000_000_000,
            },
            {"file_name": "Show.S01E01.chs-eng.ass", "size": 50_000},
        ]
    }
    result = extract_candidate_spec(share)
    self.assertEqual(result["resolution"], "2160p")
    self.assertEqual(result["dynamicRange"], "dolby_vision")
    self.assertEqual(result["videoCodec"], "hevc")
    self.assertEqual(result["audioFormat"], "atmos")
    self.assertEqual(result["subtitleClass"], "zh_en")
    self.assertEqual(result["subtitleForm"], "external")
    self.assertEqual(result["totalBytes"], 8_000_050_000)
    self.assertEqual(result["fileCount"], 2)

def test_unknown_metadata_is_reported_not_invented(self):
    result = extract_candidate_spec(
        {"list": [{"file_name": "video.mkv", "size": 100}]}
    )
    self.assertEqual(result["resolution"], "unknown")
    self.assertEqual(result["subtitleClass"], "unknown")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m unittest tests.test_media_rules
```

Expected: import or attribute failure for `extract_candidate_spec`.

- [ ] **Step 3: Implement bounded pattern extraction**

Add `extract_candidate_spec()` to `media_classifier.py`. It must inspect only
the share title and returned file names. Use case-insensitive token patterns,
sum non-negative file sizes, and derive a stable `groupKey` from:

```python
(
    resolution,
    dynamic_range,
    video_codec,
    audio_format,
    subtitle_class,
    tuple(episode_coverage),
)
```

Recognize bilingual subtitles with both Chinese and English markers in the
same subtitle name or explicit tokens such as `chs-eng`, `chi&eng`,
`中英`, or `双语字幕`. Do not classify generic `双语` as subtitle evidence when
no subtitle marker or subtitle extension exists.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 2 command.

Expected: all media-rule tests pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/media_classifier.py tests/test_media_rules.py
git commit -m "feat: extract resource specifications"
```

---

### Task 3: Deep-inspect and present every distinct option

**Files:**
- Modify: `scripts/media_service.py`
- Test: `tests/test_media_service.py`

**Interfaces:**
- Consumes: `extract_candidate_spec(details)`.
- Produces: `data.specificationGroups`, `data.remoteCandidates`, and
  `data.rejectedCandidateCounts`; each candidate remains addressable by opaque
  `candidateId`.

- [ ] **Step 1: Write failing service tests**

Create three QAS candidates: 4K bilingual, 1080p Chinese, and 1080p smaller
bilingual. Assert all are deep-read and remain visible:

```python
result = service.search("Example", "drama")
groups = result["data"]["specificationGroups"]
self.assertEqual(len(qas.reads), 3)
self.assertEqual(result["data"]["candidateCount"], 3)
self.assertEqual(
    {item["specification"]["resolution"] for item in groups},
    {"2160p", "1080p"},
)
self.assertNotIn("selectedCandidateId", result["data"])
```

Add ordering and rejection assertions:

```python
self.assertEqual(
    result["data"]["remoteCandidates"][0]["specification"]["subtitleClass"],
    "zh_en",
)
self.assertEqual(result["data"]["rejectedCandidateCounts"]["expired"], 1)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m unittest tests.test_media_service
```

Expected: ordinary search does not call `get_share()` and has no
`specificationGroups`.

- [ ] **Step 3: Rework only the existing search loop**

For each result from the existing `qas.search(query, deep=True)` call:

1. Require a share URL.
2. Call `qas.get_share(share_url, show_all=True)`.
3. Reject expired, empty, archive-only, or no-video candidates with a safe
   reason count.
4. Call `extract_candidate_spec(details)`.
5. Store `details` and `specification` with the existing opaque candidate.
6. Project safe candidate fields.
7. Group by `groupKey`.

Use this deterministic advisory ordering:

```python
subtitle_order = {"zh_en": 0, "zh": 1, "en": 2, "unknown": 3, "none": 4}
key = (
    subtitle_order.get(spec["subtitleClass"], 5),
    -int(spec["resolution"].removesuffix("p"))
    if spec["resolution"].endswith("p")
    else 0,
    spec["totalBytes"],
    candidate["candidateId"],
)
```

Do not assign or return a selected candidate.

Update `_search_update()` to attach the same specification metadata while
preserving its existing strict missing-episode selection.

- [ ] **Step 4: Run service, planner, and episode tests**

Run:

```powershell
python -m unittest tests.test_media_service tests.test_planner tests.test_episode_diff
```

Expected: all pass; planner still requires an explicit candidate ID.

- [ ] **Step 5: Commit**

```powershell
git add scripts/media_service.py tests/test_media_service.py
git commit -m "feat: list deep resource specification choices"
```

---

### Task 4: Enforce permanent protected-library deletion denial

**Files:**
- Modify: `scripts/path_guard.py`
- Modify: `scripts/organizer.py`
- Modify: `scripts/resource_agent.py`
- Test: `tests/test_path_guard.py`
- Test: `tests/test_organizer.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `PathGuard(allowed_roots, protected_roots=())`.
- Produces: `assert_deletable(path)` and `assert_replace_target(path)`.

- [ ] **Step 1: Write failing protected-root tests**

```python
guard = PathGuard(
    [downloads, library],
    protected_roots=[library],
)
with self.assertRaisesRegex(PathGuardError, "protected media library"):
    guard.assert_deletable(library / "Drama" / "Show.mkv")
with self.assertRaisesRegex(PathGuardError, "protected media library"):
    guard.assert_replace_target(library / "Drama" / "Existing")
```

Add a symlink test where a path beneath downloads resolves into the library.
Add organizer tests proving:

- an existing target is never replaced;
- successful cross-volume organization may remove only its source beneath
  downloads;
- failed cross-volume copying does not recursively delete a partial path under
  a formal library;
- cancellation invokes aria2 control only and does not call filesystem
  deletion.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m unittest tests.test_path_guard tests.test_organizer tests.test_cli
```

Expected: `PathGuard` lacks protected-root APIs and organizer cleanup still
calls `shutil.rmtree()` on its hidden library target.

- [ ] **Step 3: Implement protected-root checks**

Store real protected roots separately:

```python
self.protected_roots = tuple(
    Path(root).expanduser().resolve(strict=True)
    for root in protected_roots
)
```

`assert_deletable()` rejects any resolved path within a protected root.
`assert_replace_target()` rejects an existing target within a protected root.

Construct the default guard with:

```python
PathGuard(
    roots,
    protected_roots=[
        Path("/volume2/影视"),
        Path("/volume3/临时影视"),
    ],
)
```

Before deleting a verified staging source, call `assert_deletable(source)`.
Do not automatically delete a failed hidden copy inside a protected library;
return an organization error that identifies the safe operator action without
running cleanup.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 4 command.

Expected: all protected-root and control tests pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/path_guard.py scripts/organizer.py scripts/resource_agent.py tests/test_path_guard.py tests/test_organizer.py tests/test_cli.py
git commit -m "feat: deny formal library deletion"
```

---

### Task 5: Update the Skill contract and deploy

**Files:**
- Modify: `SKILL.md`
- Modify: `tests/test_skill_contract.py`
- Test: all `tests/`

**Interfaces:**
- Consumes: existing fixed `mediactl` commands.
- Produces: clear instructions for `drama`, full specification choices,
  explicit user selection, strict incremental behavior, and delete denial.

- [ ] **Step 1: Write failing Skill-contract assertions**

```python
self.assertIn("drama", self.frontmatter)
self.assertIn("specificationGroups", self.content)
self.assertIn("中英双语", self.content)
self.assertIn("不得自动选择", self.content)
self.assertIn("/volume2/影视", self.content)
self.assertIn("/volume3/临时影视", self.content)
self.assertIn("永远不得删除", self.content)
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
python -m unittest tests.test_skill_contract
```

Expected: the new contract assertions fail.

- [ ] **Step 3: Make the smallest Skill edit**

Update the accepted `--media-type` list. Replace the candidate guidance with a
positive output recipe:

1. Show all `specificationGroups`.
2. Show resolution, HDR/DV, codec, audio, subtitle, size, and coverage.
3. Mark bilingual subtitle options as preferred.
4. Ask the user to choose a displayed candidate.
5. Never choose a candidate automatically.

Add an explicit protected-root rule. Keep every command on the existing fixed
`mediactl` path.

- [ ] **Step 4: Run full verification**

Run:

```powershell
python -m unittest discover -s tests
git diff --check
```

Expected: all tests pass and `git diff --check` exits zero.

- [ ] **Step 5: Commit**

```powershell
git add SKILL.md tests/test_skill_contract.py
git commit -m "feat: present user-selected media variants"
```

- [ ] **Step 6: Deploy with backup**

Create a versioned archive containing `SKILL.md`, `bin`, `scripts`, and
`config`. On NAS:

1. Back up the current Skill and state database under
   `/volume4/openclaw/backups/media-skill-<timestamp>`.
2. Extract to a staging directory.
3. Atomically replace
   `/volume4/openclaw/skills/resource-download-agent`.
4. Restart only the OpenClaw container using
   `/volume4/docker/openclaw/compose.yaml`.

- [ ] **Step 7: NAS acceptance**

Run through OpenClaw:

1. Search a local-complete title and verify zero remote candidates.
2. Search a non-local title with more than one available specification and
   verify all distinct options are shown with no selected candidate.
3. Preview one user-chosen candidate without executing it.
4. Query downloads and confirm no new task exists.
5. Verify a generic delete request for either protected root is denied without
   an approval prompt.

Confirm the OpenClaw container remains healthy and
`/volume2/downloads/.incoming` has no unexpected entries.
