# PanSou and QAS Search Integration Design

## Goal

Make `resource-download-agent` use the NAS PanSou service for broad resource
discovery while retaining QAS as the authority for Quark share preview,
specification extraction, save, and download operations.

The motivating regression is `幼女战记2`: PanSou finds resources for both the
exact sequel-like title and its base title, but the current Skill searches only
QAS and therefore cannot expose PanSou candidates to OpenClaw.

## Scope

Modify the existing Skill. Do not create a second media Skill and do not change
the download, confirmation, path-safety, deletion, or library-protection rules.

The search path becomes:

```text
local NAS catalog
  -> query variants
  -> QAS discovery + PanSou discovery
  -> share-URL deduplication
  -> bounded QAS share preview
  -> specification grouping
  -> user choice
```

## Components

### PanSou client

Add `scripts/pansou_client.py`.

- Consume `PANSOU_BASE_URL`.
- Call `GET /api/search` with `cloud_types=quark`, `res=all`, and `src=all`.
- Read only `data.merged_by_type.quark`.
- Project each item to the existing candidate shape:
  `shareurl`, `taskname`, `source`, and `datetime`.
- Never expose a share URL through exceptions, `repr`, logs, or final output.
- Use a 30-second request timeout.
- Return a bounded safe error when PanSou is unreachable or returns malformed
  JSON.

### Query variants

Add a pure query-variant function.

Always retain the exact user query. When a query ends with an attached integer
from 1 through 20 and has a non-numeric base title of at least two characters,
also generate:

1. the base title;
2. `<base> 第N季`;
3. `<base> SNN`.

Do not expand a four-digit year or an all-numeric title. Preserve order and
remove duplicate variants.

For `幼女战记2`, the variants are:

```text
幼女战记2
幼女战记
幼女战记 第2季
幼女战记 S02
```

### Discovery aggregation

Keep local-library lookup first. If the local result satisfies a normal search,
return it without calling either remote source.

For remote search:

- query QAS and PanSou for every variant;
- preserve source provenance internally;
- deduplicate by normalized Quark share URL;
- keep the first occurrence in variant/source order;
- process QAS candidates before PanSou candidates for the same variant;
- preview every retained candidate through `QasClient.get_share`;
- discard invalid, expired, non-Quark, archive-only, or unpreviewable shares;
- enforce `PANSOU_MAX_CANDIDATES=50` as the maximum number of unique PanSou
  candidates admitted to deep preview;
- keep existing specification extraction, bilingual-subtitle preference, and
  user-choice output.

The limit applies after URL deduplication and does not reduce QAS candidates.

### Failure behavior

- QAS remains required because preview and execution depend on it.
- A PanSou failure does not fail the whole search if QAS remains available.
- A degraded search returns a bounded `warnings` entry such as
  `pansou_unavailable`; it does not include endpoints, links, response bodies,
  stack traces, or credentials.
- If both sources produce no valid previewable candidate, return
  `nextAction=no_candidates`.
- Search never downloads, saves, deletes, organizes, or mutates a media
  library.

## Configuration

Add these Skill environment variables:

```text
PANSOU_BASE_URL
PANSOU_MAX_CANDIDATES
```

`PANSOU_BASE_URL` is required for the deployed NAS configuration.
`PANSOU_MAX_CANDIDATES` defaults to `50`, accepts integers from 1 through 100,
and falls back to 50 when unset or invalid.

Store the real NAS endpoint only in OpenClaw's private configuration. Do not
commit it to GitHub.

## Output Contract

Continue returning opaque `candidateId` values and grouped specifications.
Do not expose PanSou URLs, QAS URLs, share links, tokens, cookies, raw candidate
objects, or internal source responses.

Candidates may include a safe `discoverySources` list containing only
`qas` and/or `pansou`.

## Tests

Add tests before implementation for:

- `幼女战记2` produces the four ordered variants;
- years and numeric-only titles are not broadened;
- PanSou response projection accepts the documented result shape;
- malformed PanSou data raises a bounded error without leaking URLs;
- QAS and PanSou duplicate share URLs collapse to one candidate;
- PanSou-only candidates are previewed through QAS and reach specification
  groups;
- a PanSou outage preserves QAS results and adds
  `pansou_unavailable`;
- the 50-candidate PanSou limit applies after deduplication;
- local-first hits call neither QAS nor PanSou;
- final JSON contains no share URL or endpoint.

Run the complete existing test suite after the focused tests.

## Deployment

1. Back up the installed Skill and OpenClaw configuration.
2. Add the private PanSou environment values to
   `skills.entries.resource-download-agent.env`.
3. Synchronize the tested Skill files to
   `/volume4/openclaw/skills/resource-download-agent`.
4. Run the complete test suite inside the OpenClaw container.
5. Restart only the OpenClaw gateway if configuration reload requires it.
6. Ask OpenClaw to search `幼女战记2` in preview-only mode.
7. Verify the result includes selectable candidates and performs no download.
8. Roll back the Skill and configuration if the focused live test fails.
