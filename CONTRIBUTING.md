# Contributing to Sheshnag

The development process, stated explicitly so nobody has to guess. If something
here is wrong or slows you down for no reason, open an issue and change it —
but change it deliberately rather than working around it.

---

## The one rule

**Nothing reaches `main` except a pull request that someone other than its
author has approved and merged.**

Everything below is detail. If you remember one line, remember that one.

---

## Workflow

1. **Start from an issue.** If the work isn't in an issue yet, file one first.
   The issue is where scope gets agreed; the PR is where code gets reviewed.
   Disagreements about *what* to build belong on the issue, not in review.

2. **Branch.** Never commit on `main`.

   ```bash
   git checkout main && git pull
   git checkout -b task/<short-slug>          # task/ollama-structured-outputs
   ```

   Prefix by intent: `task/` for issue work, `fix/` for bug fixes,
   `docs/` for documentation-only changes.

3. **Commit to the branch.** Stage the files you changed — `git add <paths>`,
   never `git add -A`. Write a message that says what changed and why; the
   diff already says how.

4. **Verify before you open the PR.** Run whatever exists for the area you
   touched (see [Testing](#testing)). "It worked on my machine" without a
   command you can name is not verification.

5. **Open the PR** against `main` and fill in the template. Link the issue
   with `Closes #NN`.

6. **Someone else reviews and merges.** Not you. See below.

---

## Who merges

| Situation | Who merges |
|---|---|
| Normal PR | A reviewer who is not the author |
| Your own PR, approved by someone else | Still the reviewer, or ask them explicitly |
| Nobody has reviewed it | **Nobody merges it.** Ask in the group, wait. |
| Urgent production fix | Ping @Ankush-Chander. There is no self-merge exception. |

Self-merging is the specific thing this document exists to prevent. A PR you
wrote and merged yourself received no review, regardless of how long it sat
open or how sure you are about it.

**Never do any of these:**

- `git push` to `main`
- `git push --force` / `--force-with-lease` to any shared branch
- Merge your own PR
- Merge a PR with unresolved review comments
- Rewrite history on a branch someone else has pulled

---

## Reviewing

- **Approve** means "I would be comfortable if this broke and my name was on
  it." It does not mean "I skimmed it and nothing jumped out."
- **Request changes** when something must change before merge. Say which line
  and why.
- **Comment** for observations that do not block. If you want to block, use
  Request changes — a comment does not stop anyone from merging.
- Review the *diff*, not the description. PR bodies describe intent; the diff
  is what ships. These have differed before.

---

## Testing

| Area | Command | State |
|---|---|---|
| Backend | `cd backend && python -m pytest tests/ -q` | needs a **throwaway** Postgres — `TEST_DATABASE_URL`, default `postgresql://postgres:postgres@localhost:5432/sheshnag_test`. Every table is dropped at session start and end, so never point it at a database you care about, or at `DATABASE_URL` |
| Daemon | `cd daemon && pip install -e ".[dev]" && python -m pytest tests/ -q` | mock backend + mock runtimes in `daemon/tests/` |
| Frontend | `npm run lint` | no test framework yet |

Two traps that have already cost time:

- **Daemon tests need `pytest-asyncio`.** It is in the `[dev]` extra, not in
  `daemon/requirements.txt`. Install with `pip install -e ".[dev]"` or the
  async tests silently fail to run rather than passing.
- **`backend/.gitignore` ignores `test_*.py`.** New backend tests need
  `git add -f`. If you write a test and it doesn't appear in `git status`,
  this is why.

If you touch behaviour that has a test, run that suite before opening the PR.
If you fix a bug, add a test that fails without your fix — see
`dau-mcp-server/tests/test_chat_guardrails.py` for the standard: name the
incident in the docstring so the test survives the next cleanup.

---

## AI agents

Agents (Claude Code, Codex, Cursor, …) are welcome and used heavily here.
They follow the same rules, plus a few of their own, in
[`AGENTS.md`](AGENTS.md).

**If you are running an agent, you are accountable for what it does.** An
agent that pushes to `main` or merges a PR is your push and your merge. Read
its plan before approving actions, and never leave it in a mode where it can
run `git push` or `gh pr merge` unattended.

`.claude/settings.json` denies those commands for Claude Code specifically.
That is a seatbelt, not a wall — it does not bind other tools, and it does not
bind a human typing the same command.

---

## Repository conventions

- **Docs:** [`docs/develop.md`](docs/develop.md) is canonical for local setup; component
  READMEs point at it rather than repeating it.
- **Models:** every servable model is a pinned catalogue entry. See
  [`docs/reference/model-catalogue.md`](docs/reference/model-catalogue.md) — there is no
  "run an arbitrary model" path, by design.
- **Secrets:** never commit `.env`. `backend/.env.example` documents every
  variable the backend reads; add to it when you add a variable.

---

## Writing documentation

The docs under `docs/` are a MkDocs site, organised by **who is reading**, not
by which component a thing belongs to. Six rules keep it that way.

1. **Route by audience.** There are four guides —
   [using-sheshnag](docs/using-sheshnag.md),
   [provider](docs/provider.md), [self-host](docs/self-host.md),
   [develop](docs/develop.md) — and everything is reachable in one hop from one
   of them. A page that belongs to no audience is homeless; find it a home or
   do not write it.
2. **One genre per file.** Guide (do this, in order) · Reference (look this up) ·
   Spec (what we intend). Never two in one file. Mixing a localhost quickstart
   into a production runbook is exactly what this refactor undid.
3. **Every guide ends in a verified state** — a "check it worked" section with a
   command and the output to expect.
4. **One canonical home per fact.** Everything else links. Component READMEs are
   stubs that point into `docs/`; keep them that way.
5. **Date every page.** A `Last updated` or `Verified against code:` line near
   the top. If you did not re-verify it, say so rather than restamping it — a
   false date is worse than an old one.
6. **Link style is not a preference — the build enforces it:**

   | Target | Style |
   |---|---|
   | Another page under `docs/` | relative, keep `.md` — `[Setup](develop.md)` |
   | Code, or anything outside `docs/` | `https://github.com/gamekeepers/sheshnag/blob/develop/…` |

   MkDocs resolves relative links against `docs/`, so a relative path to
   something outside it cannot resolve and fails the build.

**Run `mkdocs build --strict` before opening a PR that touches `docs/`.** It
fails on broken links, broken in-page anchors, and nav entries pointing at
missing files. CI runs the same command.

```bash
python3 -m venv .venv-docs
.venv-docs/bin/pip install -r docs/requirements.txt
.venv-docs/bin/mkdocs serve          # preview at http://127.0.0.1:8000
.venv-docs/bin/mkdocs build --strict # what CI runs
```
