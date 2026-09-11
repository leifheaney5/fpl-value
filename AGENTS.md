<!--
  AGENTS.md — PROVIDER-NEUTRAL.

  Plain Markdown instructions for any AI coding assistant working in this
  repository. Nothing here is specific to one vendor's agent, skill, or command
  format, so it remains useful regardless of which tool is in use.

  Keep it short and factual. Replace every TODO.
-->

# Agent instructions

Instructions for any AI assistant working in this repository. Human
contributors should read `CONTRIBUTING.md`.

## Required workflow

1. **Orient before editing.** Read this file, `CLAUDE.md` if present, and the
   README. Run `git status` and note uncommitted work.
2. **Plan before substantial change.** Anything touching more than a couple of
   files, or touching TODO: *list the sensitive areas here*, needs an agreed
   plan first.
3. **Make the smallest complete change.** No unrelated refactoring, renaming, or
   reformatting.
4. **Verify.** Run focused checks, then the canonical verification command.
5. **Review your own diff** before reporting completion.
6. **Report exact commands and actual results.** Never claim a check passed
   without having run it.

## Conventions

TODO: the conventions that are not obvious from reading the code.

- Naming: TODO
- Error handling: TODO
- Logging: TODO
- Imports and module layout: TODO
- Formatting: TODO — state the tool, so it is not done by hand

Where this section is silent, follow the conventions of the file you are
editing.

## Tests

- Framework: TODO
- Location and naming: TODO
- Run one file: TODO
- Run everything: TODO
- Expectation: TODO — e.g. "every behavioural change needs a test", or "tests
  are required for `src/core/` and optional elsewhere"

Never weaken, skip, or delete a test to make a suite pass. If a test blocks a
legitimate change, say so and explain why.

## Documentation

- Update documentation the change actually affects; leave the rest alone.
- Verify every command and path you document.
- TODO: does this project keep a changelog? What format?
- TODO: where does API documentation live, and is it generated?

## Dependencies

- **Adding, upgrading, or removing a dependency requires explicit approval.**
- TODO: the package manager and the exact command.
- TODO: lockfile policy — committed? updated how?
- TODO: any licence or provenance restrictions.

## Git

- TODO: branch naming.
- TODO: commit message format.
- TODO: does this project squash, rebase, or merge?
- Do not commit unless asked.
- **Never** push, force-push, merge, tag, or rewrite history without an explicit
  request for that specific action.

## Definition of done

A change is done when:

- [ ] It satisfies the stated requirements.
- [ ] TODO: canonical verification command passes.
- [ ] Tests exist for new or changed behaviour.
- [ ] Documentation affected by the change is updated.
- [ ] The diff has been reviewed and contains nothing unrelated.
- [ ] TODO: any project-specific gate.

## Never do these without asking

- Deploy, or touch any production system.
- Access production data.
- Read or retrieve secrets. Do not open `.env` files or credential stores.
- Push, merge, or publish.
- Run migrations against anything but a local development database.
- Modify CI/CD or infrastructure configuration.
- TODO: anything else specific to this project.
