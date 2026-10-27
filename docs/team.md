# Team and ownership

One named owner per track, so every piece of the finetuning work has someone who
answers for it end to end. Decided under G4. The proposal and sign-off are in
the role-split doc: https://claude.ai/artifact/NDrzxacSXbJWpzUcN5UKAZ

| Track | Scope | Owner |
|---|---|---|
| Training vertical | Engine (LLaMA-Factory), runner, generated configs, eval, watchtower runs | _TBD — decide by Friday_ |
| Platform surface | `/v1/fine_tuning/*` API, schema, dataset validator, dashboard UI, adapter serving | _TBD — decide by Friday_ |

## The seam

Serving belongs to the platform surface, but it consumes the training
vertical's artifact. The interface between the two is the runner protocol and
the adapter artifact in [`finetune_contract.md`](finetune_contract.md).

A change to any of these needs **both owners' agreement first**:

- the LoRA limits: allowed ranks, target modules, and the base-model pinning
- the `/workers/finetune-*` endpoints and their payloads
- the fields of `finetune_artifacts`

The platform fails any adapter it cannot serve. So a training-side change to
rank, modules or base model that skips this breaks jobs rather than surfacing
as a review comment.
