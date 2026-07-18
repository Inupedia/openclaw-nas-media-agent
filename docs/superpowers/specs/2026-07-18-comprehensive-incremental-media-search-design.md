# Existing Media Skill Enhancement Design

## Goal

Improve the existing `resource-download-agent` without introducing a new
classification system, search platform, or workflow.

## Changes

### Add `drama`

- Accept `drama` in `--media-type`.
- Route `drama` to the same cloud, staging, final-library, naming, and episode
  rules as `tv`.
- Prefer `drama` for newly classified scripted episodic media.
- Keep `tv` compatible with existing tasks and state.

### Keep downloads incremental

- A local movie remains terminal.
- Episodic downloads contain only locally missing episodes.
- Episodes already local, active, or reserved by a pending plan remain
  excluded.
- If a collection cannot select only missing episodes, stop with
  `incremental_selection_unavailable`.
- Do not implement replacement or quality upgrades.

### Protect formal libraries from deletion

OpenClaw must never delete, overwrite, clean, or move content out of:

- `/volume2/影视`
- `/volume3/临时影视`

Pause, resume, and cancel remain available. Cancel preserves downloaded data.
Verified staging content under `/volume2/downloads` may be removed only after a
successful, confirmed organization operation.

### Show resource specifications and let the user choose

Use the existing QAS/PanSou deep search. Inspect every returned viable
candidate and display all distinct options instead of selecting the highest
score.

For each candidate report when detectable:

- Resolution.
- HDR, Dolby Vision, or SDR.
- Video codec.
- Audio format.
- Chinese-English bilingual, Chinese, English, none detected, or unknown
  subtitle status.
- Embedded or external subtitle form.
- Total size.
- File count.
- Season and episode coverage.

Group equivalent candidates by these specifications. Keep every distinct
quality, subtitle, coverage, and size option visible. Order bilingual subtitle
options first, but do not select them automatically.

The user chooses a displayed opaque `candidateId`. If the choice is ambiguous,
ask the user instead of silently breaking the tie. Preview, plan, confirmation,
staging, validation, and organization remain the existing workflow.

## Tests

- `drama` is accepted and routes like `tv`.
- Existing `tv` tasks still work.
- Search deep-inspects all returned candidates.
- 4K and 1080p, large and small, and differing subtitle options remain visible.
- Bilingual subtitles sort first without automatic selection.
- Episodic candidates contain only missing episodes.
- Cancel preserves data.
- Delete, overwrite, cleanup, and move-out operations beneath both protected
  roots are rejected, including symlink-mediated attempts.
- Existing files in protected roots are never replaced.
