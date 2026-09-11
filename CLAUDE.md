<!--
  Project-level CLAUDE.md — Claude-specific file, provider-neutral content.

  PURPOSE: record facts about THIS project that a global orchestration layer
  cannot know. Do not restate generic engineering process here — the global
  layer already supplies workflow, safety tiers, and evidence requirements.

  KEEP IT SHORT. This file is loaded into every session. One screen is the
  target; move detail into docs/ and link to it.

  Replace every TODO. A TODO is honest; a guess in a file loaded every session
  is a durable lie.
-->

# <PROJECT NAME>

TODO: One or two sentences. What this project is, and who or what uses it.

## Technologies

TODO: Primary languages, runtimes and their versions, frameworks, and the
package manager. Only what someone would need to know before touching the code.

## Architecture

TODO: Three to five sentences, or a short list of the major components and the
direction of dependency between them. Link to a fuller document rather than
expanding this section.

Authoritative detail: TODO: `docs/ARCHITECTURE.md`, or delete this line.

## Key directories

| Path | Contains |
| --- | --- |
| TODO: `src/` | TODO |
| TODO: `tests/` | TODO |

List only the directories that matter. A complete tree belongs in a README.

## Commands

Every command below must be **copy-pasteable and verified**. Delete any row you
have not actually run.

| Purpose | Command |
| --- | --- |
| Install dependencies | TODO |
| Run locally | TODO |
| Build | TODO |
| Lint | TODO |
| Format | TODO |
| Type-check | TODO |
| Run one test file | TODO |
| Run all tests | TODO |

### Canonical verification

TODO: the single command that must pass before work is considered done.

```
TODO
```

This is the most valuable line in this file. Without it, every session has to
re-derive it from CI, and may get it wrong.

## Generated files — do not edit by hand

TODO: paths, and the command that regenerates each. Write "none" if none.

## Protected paths — require explicit approval

TODO: migrations, infrastructure, CI configuration, lockfiles, vendored code,
anything with a special review process. Write "none" if none.

## Project constraints

TODO: anything that would surprise someone competent. Supported platform or
browser matrix, backward-compatibility guarantees, performance budgets,
regulatory requirements, licence restrictions on dependencies, house rules that
differ from the language's norms. Write "none" if none.

## Deployment boundaries

TODO: how this is deployed, and by whom. State explicitly what an AI assistant
must never do here — typically: deploy, push, merge, publish, touch production,
or run migrations against anything but a local database.

## Authoritative documents

| Topic | Document |
| --- | --- |
| TODO | TODO |

When a document and this file disagree, TODO: state which wins.

## Notes for AI assistants

TODO: project-specific expectations only. Do not repeat the global layer's
workflow, safety tiers, or evidence rules here.

Examples of what belongs here:
- "Never modify anything under `src/generated/` — regenerate with `<command>`."
- "This repository squashes on merge; do not create merge commits."
- "Integration tests need a local Postgres; `<command>` starts one."
