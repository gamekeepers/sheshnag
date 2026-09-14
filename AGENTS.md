# Sheshnag — Batch AI Compute Platform

Instructions for any coding agent working in this repo (Claude Code, Codex,
Cursor, …). Humans: the same process rules apply — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

> The repository and the product are both named **Sheshnag**. `moonknight` is
> the old name and still appears in some older prose; GitHub redirects the old
> URL. Use Sheshnag in anything new, and fix stale mentions as you pass them.

---

## Rules for agents — read before touching anything

These are hard constraints, not preferences.

1. **Never push.** No `git push`, to any branch, ever. A human pushes.
2. **Never merge.** No `gh pr merge`, no `git merge` into `main`, no
   "the PR looked approved so I merged it".
3. **Never open a PR.** Prepare the branch and the description; a human opens it.
4. **Never work on `main`.** Create `task/<slug>` before editing a single file.
   If the working tree is already dirty, branch from HEAD and leave those
   changes alone.
5. **Never rewrite shared history** — no force-push, no rebase of a pushed
   branch, no `git reset --hard` on a branch that exists on the remote.
6. **Stage only what you changed.** `git add <paths>`, never `git add -A`.
   This repo routinely has unrelated untracked files in the tree.
7. **Verify before you report done.** Run the relevant suite (below) and say
   what you ran and what it printed. "Should work" is not a result.

`.claude/settings.json` denies the push/merge/rebase commands for Claude Code.
Treat that as a backstop for mistakes, not as the boundary of what is allowed —
the rules above bind you regardless of which tool you are.

**If a task seems to require pushing or merging, stop and say so.** That is a
signal the task needs a human, not a signal to find a way around the rule.

---

## Where everything else lives

This file used to carry the architecture, the commands, the test matrix and the
per-component quirks. They now live in the documentation, which is built and
link-checked in CI, so there is one copy rather than two that drift.

**Read [`docs/develop.md`](docs/develop.md) before your first change.** It has
the three-service architecture, how to run each of them locally, mock mode for
working without a GPU or a backend, the test commands and their traps, a manual
end-to-end runbook, and the quirks that used to be listed here — no TypeScript
on the frontend, `create_all()` never adding columns to an existing table,
import-time work breaking pytest collection, the daemon's config precedence, and
`OllamaExecutor._translate_request`'s hard whitelist.

| You need | Read |
|---|---|
| To change any code | [`docs/develop.md`](docs/develop.md) |
| Every environment variable | [`docs/reference/configuration.md`](docs/reference/configuration.md) |
| The API contract | [`docs/reference/api.md`](docs/reference/api.md) |
| Model rules — there is **no path to run an uncatalogued model** | [`docs/reference/model-catalogue.md`](docs/reference/model-catalogue.md) |
| The database shape | [`docs/reference/data-model.md`](docs/reference/data-model.md) |
| To deploy it | [`docs/self-host.md`](docs/self-host.md) |
| Process — branching, review, who merges | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

Two things worth repeating here because they cost time and an agent will hit
them before it opens the docs:

- **The backend test suite drops every table** in `TEST_DATABASE_URL` at session
  start and end. Never point it at `DATABASE_URL` or any database that matters.
- **`backend/.gitignore` ignores `test_*.py`.** A new backend test needs
  `git add -f` or it is silently never committed.

If you change behaviour these documents describe, update them in the same PR and
run `mkdocs build --strict` — it fails on broken links and anchors.
