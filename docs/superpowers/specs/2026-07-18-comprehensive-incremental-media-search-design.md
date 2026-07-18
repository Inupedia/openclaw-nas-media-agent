# Comprehensive Incremental Media Search Design

## Objective

Upgrade `resource-download-agent` so OpenClaw searches broadly, presents
meaningfully different resource specifications, and lets the user choose.
The agent must remain incremental-only and must never delete content from the
two formal media-library roots.

## Scope

This change adds:

- `drama` as a supported media type.
- Deep, multi-query resource discovery.
- Candidate inspection, deduplication, specification extraction, and grouping.
- User-controlled candidate and file selection.
- Hard delete protection for formal media libraries.

This change does not add automatic replacement, quality upgrades, media-library
cleanup, or autonomous version selection.

## Media Types

The accepted `--media-type` values are:

- `movie`
- `drama`
- `tv`
- `anime`
- `documentary`
- `show`
- `other`

`drama` is the preferred type for scripted episodic series. It uses the same
storage root, episode detection, naming, and incremental rules as the existing
`tv` type. `tv` remains supported so existing tasks, state records, and callers
continue to work.

## Search Pipeline

### NAS-first gate

Every title request starts with the bounded local catalog.

- A complete local movie is terminal.
- A complete episodic title with no missing episodes is terminal unless the
  user explicitly requests an update check.
- An update search may continue only for episodes absent locally and absent
  from active downloads and unconsumed plans.

### Query expansion

When remote discovery is allowed, the search service builds bounded query
variants from known metadata:

- Chinese title.
- English or original title when available.
- Release year when available.
- Season number for episodic titles.
- A normalized title without decorative or technical terms.

The service queries every available resource-search route for each distinct
variant and unions the responses. It records which query found each result but
does not expose raw share URLs.

### Deep inspection

Search may take approximately 30–90 seconds. Every viable result is inspected
before presentation so the service can report actual files instead of relying
only on misleading share titles.

Failed, expired, archive-only, empty, or non-media candidates are excluded and
reported as aggregate rejection counts. A partial provider failure must not
discard valid results from other queries.

### Deduplication

Candidates are deduplicated by stable evidence in this order:

1. Provider share identity.
2. Normalized media-file manifest.
3. Normalized title, episode coverage, total bytes, and file count.

Equivalent shares become variants inside one specification group. No valid
distinct specification is silently discarded.

## Specification Extraction

Each candidate exposes only safe, user-relevant metadata:

- Opaque `candidateId`.
- Normalized title and release year.
- Media type.
- Season and episode coverage.
- Resolution: 2160p, 1080p, 720p, SD, or unknown.
- Dynamic range: Dolby Vision, HDR10/HDR, SDR, or unknown.
- Video codec: AV1, HEVC/H.265, H.264, or unknown.
- Audio format when detectable, such as Atmos, TrueHD, DTS, AAC, or unknown.
- Subtitle class:
  - Chinese-English bilingual.
  - Chinese.
  - English.
  - No subtitle detected.
  - Unknown.
- Subtitle form: embedded, external, mixed, or unknown.
- Total bytes, media-file count, and average bytes per episode where relevant.
- Completeness and missing-episode coverage.
- Warnings such as mixed resolution, suspicious extras, or ambiguous naming.

Raw Cookie, token, RPC secret, authorization header, provider response, and
share URL remain private.

## Grouping and Presentation

Results are grouped by the properties that materially affect a download
decision:

- Episode or movie coverage.
- Resolution.
- Dynamic range.
- Video codec.
- Audio format.
- Subtitle class.
- Approximate size band.

The JSON response returns every group and every selectable candidate within
that group. A normal answer summarizes groups in a compact table and can expand
their individual candidates.

Ordering is advisory:

1. Exact title, year, season, and missing-episode match.
2. Chinese-English bilingual subtitle.
3. Chinese subtitle.
4. Known subtitle status.
5. Smaller size within otherwise equivalent specifications.
6. Unknown or ambiguous metadata.

The service may attach badges such as `bilingual_subtitle_preferred`,
`smallest_equivalent_variant`, or `exact_episode_coverage`. A badge is not a
selection. The service must not return an automatically chosen candidate.

The default response does not truncate valid specification groups to a small
top-N list. If the transport size bound is reached, it returns a cursor and
`nextAction: list_more_candidates`; it never pretends the first page is the
complete result set.

## User Selection

The user may select:

- A displayed group and candidate.
- A resolution, subtitle, codec, dynamic-range, or size preference.
- Exact missing episodes within a selectable episodic candidate.

The agent resolves natural-language choices to a displayed opaque
`candidateId`. If more than one candidate still matches, it asks the user to
choose; it does not break the tie silently.

Planning requires a previously deep-inspected candidate. A plan contains only
the selected candidate and exact selected files. Execution remains a separate
confirmation step.

## Incremental-only Rules

The agent may add new media but may not replace or remove formal-library media.

- Existing movie: stop; no alternate version download.
- Episodic media: select only missing episodes.
- Exclude episodes present locally.
- Exclude episodes in active downloads.
- Exclude episodes reserved by an unconsumed plan.
- If a remote collection cannot select only the missing episodes, return
  `incremental_selection_unavailable`.
- Quality upgrade, downgrade, replacement, edition swap, and overwrite are out
  of scope.

## Deletion and Cancellation Safety

The immutable protected roots are:

- `/volume2/影视`
- `/volume3/临时影视`

No Skill command may unlink, recursively remove, overwrite, replace, move out,
or clean content beneath either root. This rule still applies when the user
explicitly asks OpenClaw to delete library media.

Allowed operations:

- Pause an owned download task.
- Resume an owned download task.
- Cancel an owned download task while preserving downloaded data.
- Copy and verify completed staging content into a protected library.
- Remove the verified source from `/volume2/downloads` only as the final step
  of a user-confirmed organization plan.
- Clean staging content under `/volume2/downloads` only through a distinct,
  user-confirmed cleanup plan.

All path mutation must pass a real-path containment guard. Symlinks and
resolved targets outside the intended staging root are rejected.

## Interfaces

The fixed executable remains:

`/root/.openclaw/workspace/skills/resource-download-agent/bin/mediactl`

Search remains:

`mediactl search TITLE [--media-type TYPE] [--update]`

The response schema gains:

- `searchCoverage`
- `rejectedCandidateCounts`
- `specificationGroups`
- `candidateCount`
- `hasMore`
- `nextCursor`

Preview remains candidate-specific:

`mediactl preview CANDIDATE_ID`

Download planning remains candidate-specific:

`mediactl plan download CANDIDATE_ID`

No delete-library command will be added.

## Error Handling

- Provider/query failure: continue other bounded queries and report partial
  coverage.
- Candidate inspection failure: reject that candidate with a safe reason code.
- Ambiguous specification: keep the candidate, mark unknown fields, and avoid
  false claims.
- No selectable incremental files: stop with a specific `nextAction`.
- Pagination required: return a cursor, not a silently truncated list.
- Protected-root mutation attempt: return `PROTECTED_LIBRARY_DELETE_DENIED`.

## Testing

Automated tests must cover:

- `drama` CLI acceptance and routing equivalence with `tv`.
- Compatibility of existing `tv` state and tasks.
- Multi-query union and deterministic deduplication.
- Deep extraction of resolution, HDR/DV, codec, audio, size, coverage, and
  subtitle class.
- Chinese-English bilingual subtitle ordering without automatic selection.
- Multiple size/quality variants remain visible.
- Pagination preserves all candidates.
- No candidate is auto-selected.
- Existing and active episodes are excluded.
- Non-selectable collections stop safely.
- Cancellation preserves downloaded data.
- Direct and symlink-mediated delete/move/overwrite attempts against both
  protected roots fail.
- Confirmed organization may delete only its verified staging source.

NAS acceptance tests must verify:

- A local-complete title remains terminal with zero remote calls.
- A remote title displays several specification choices when available.
- Selecting one displayed candidate creates a preview/plan but no download
  until confirmation.
- Download destinations remain under `/volume2/downloads/.incoming`.
- Protected-library mutation probes are denied.

## Success Criteria

- The user sees the breadth of valid resource specifications rather than a
  system-selected result.
- Subtitle, quality, codec, coverage, and size trade-offs are visible.
- Chinese-English bilingual subtitles receive an advisory preference.
- Every episodic plan is a strict missing-episode delta.
- No OpenClaw workflow can delete or move media out of either formal library.
- Existing `tv` tasks continue to function while new drama searches can use
  `--media-type drama`.
