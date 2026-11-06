# Sheshnag Forge: team doc

**Team:** Manan Ghonia, Ved Mungra · **Driver:** AC
**Status:** G4 settled on 2026-10-26. Roles are decided and the job contract is agreed.

## What we are building

Sheshnag Forge is the finetuning part of Sheshnag:

1. A user uploads a chat dataset.
2. Sheshnag trains a LoRA adapter on it.
3. The user calls the result by name.

The goal is to *submit a dataset, get an adapter, and call it, without ever
downloading a file*. Forge is an extension of Sheshnag, not a separate
service: same API key, same `/v1/files` upload, same backend.

## Who owns what

The work splits into two tracks. We decided to own both **jointly**, as we
have worked so far.

| Track | Scope | Owner |
|---|---|---|
| Training vertical | Engine (LLaMA-Factory), forge-runner, generated configs, eval, watchtower runs | Manan + Ved (joint) |
| Platform surface | `/v1/fine_tuning/*` API, schema, dataset validator, dashboard UI, adapter serving | Manan + Ved (joint) |

G4 was raised because the project stalled for three weeks with nobody owning
anything. Joint ownership only works if **every task on the board names who
is doing it this week**. If work stalls again, we split the tracks, with one
named owner each.

## The job contract (agreed 2026-10-26)

Both of us agreed the finetuning job contract. The full text is in
[`finetune_contract.md`](finetune_contract.md). These are the decisions that
are expensive to change later:

| Decision | What | Why |
|---|---|---|
| Routes | `/v1/fine_tuning/jobs`: create, list, get, cancel, events, artifacts | Mirrors OpenAI's fine-tuning API on purpose |
| Statuses | validating_files → queued → running → succeeded / failed / cancelled | OpenAI's vocabulary |
| LoRA rank | 8, 16, 32 or 64 only | Serving runs vLLM with `--max-lora-rank 64`, and a larger adapter stops the whole server from starting (G1) |
| Target modules | `all-linear` or a subset of the 7 projection modules | The only set G1 proved servable |
| Base model | Pinned on the job; the catalogue entry must be a commit SHA | vLLM never checks an adapter's base, and a wrong one serves garbage with HTTP 200 (G1) |
| Unknown request keys | Rejected (HTTP 422) | Axolotl silently dropped a typo'd key in G2, and the API must not |
| Engine | LLaMA-Factory 0.9.5, pinned by image digest | G2 winner |
| Adapter | The backend reads `adapter_config.json` from the uploaded zip, and the job succeeds only if the adapter is servable | An adapter that cannot be served is not a deliverable |

## The seam

Serving belongs to the platform surface, but it consumes the artifact that
the training vertical produces. This is the seam to watch all semester. The
interface between the two tracks is the runner protocol
(`/workers/finetune-*`) and the adapter artifact, both specified in
[`finetune_contract.md`](finetune_contract.md).

A change to any of these needs **agreement from both Manan and Ved first**:

- the LoRA limits: allowed ranks, target modules, and base-model pinning
- the `/workers/finetune-*` endpoints and their payloads
- the fields of `finetune_artifacts`

The platform fails any adapter it cannot serve. A training-side change that
skips this agreement therefore breaks jobs at runtime instead of being caught
in review.

## G4 sign-off

- [x] Each track has a named owner: Manan + Ved, jointly
- [x] Written into the team doc and the project page: this file, linked from the `sheshnag-finetuning` space
- [x] Job contract agreed together and published: agreed 2026-10-26, see [`finetune_contract.md`](finetune_contract.md)
