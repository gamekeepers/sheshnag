# Sheshnag

**Distributed batch AI compute.** Sheshnag pools idle GPUs from labs and
researchers and runs asynchronous, OpenAI-style JSONL batch inference on them.
Users submit batches through a dashboard or the API; organisations lend GPUs by
running one lightweight daemon; a control plane validates, schedules, tracks and
returns results.

It is **software you host**, not a service you sign up for. Everything below
assumes someone runs their own deployment.

---

## Who are you?

Pick the one that describes why you are here. Each guide is meant to be read
front to back, once.

### I have a GPU to lend

You want your machine to pick up jobs from a deployment someone else runs. One
command, one key, about ten minutes — no repository clone, no Python, no
database, no `sudo`.

**→ [Lend your GPU to Sheshnag](provider.md)**

### I want to run Sheshnag for my institution

You are standing up the control plane on your own premises: TLS, an admin
account, the model catalogue, the first provider onboarded.

**→ [Run Sheshnag for your institution](self-host.md)**

### I want to change the code

Frontend, backend and daemon running locally, tests green, and the contract in
front of you.

**→ [Work on Sheshnag](develop.md)**

### I want to submit jobs

Someone already runs a deployment and gave you a URL and a key. You swap
`base_url`, submit a JSONL batch, poll it, download the results.

**→ [Submit batches to Sheshnag](using-sheshnag.md)**

---

## Reference

Look these up; do not read them front to back.

| Page | What it answers |
|---|---|
| [OpenAI compatibility](reference/openai-compatibility.md) | Which OpenAI parameters are honoured, ignored, or rejected |
| [Structured outputs](reference/structured-outputs.md) | Getting schema-constrained JSON out of a batch |
| [Model catalogue](reference/model-catalogue.md) | How a model becomes servable — curation, pinning, digests |
| [Data model](reference/data-model.md) | Tables, relationships, and why they are shaped that way |
| [Google OAuth](reference/google-oauth.md) | Configuring Google sign-in for a deployment |
| [Machine inspection](reference/machine-inspection.md) | What the daemon detects about a host, and how |

## Spec

[v1 spec](spec/v1-spec.md) — **intent, not a description of what exists.** It
states what Sheshnag is meant to be and may lag the implementation. Where the
spec and the code disagree, the code is the fact and the spec is the bug.

## Elsewhere in the repository

These live outside the documentation site because they address contributors and
agents rather than product readers:

- [`README.md`](https://github.com/gamekeepers/sheshnag/blob/develop/README.md) — repository overview
- [`CONTRIBUTING.md`](https://github.com/gamekeepers/sheshnag/blob/develop/CONTRIBUTING.md) — process, conventions, review
- [`AGENTS.md`](https://github.com/gamekeepers/sheshnag/blob/develop/AGENTS.md) — rules for coding agents

The component READMEs are stubs now: the API contract is
[API reference](reference/api.md) and the daemon's internals are
[Daemon internals](reference/daemon.md).

---

*Sheshnag is licensed under the Apache License 2.0 — see
[`LICENSE`](https://github.com/gamekeepers/sheshnag/blob/develop/LICENSE).*
