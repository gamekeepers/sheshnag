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
| Backend | `cd backend && python -m pytest tests/ -q` | needs a throwaway Postgres — `TEST_DATABASE_URL`, default `postgresql://postgres:postgres@localhost:5432/sheshnag_test`; dropped and recreated each run |
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

- **Docs:** [`docs/setup.md`](docs/setup.md) is canonical for setup; component
  READMEs point at it rather than repeating it.
- **Models:** every servable model is a pinned catalogue entry. See
  [`docs/model_catalogue.md`](docs/model_catalogue.md) — there is no
  "run an arbitrary model" path, by design.
- **Secrets:** never commit `.env`. `backend/.env.example` documents every
  variable the backend reads; add to it when you add a variable.
