# Team and ownership

Decided 2026-10-26 (G4): Manan and Ved own both tracks jointly, as they have
worked so far. The decision and sign-off are in the role-split doc:
https://claude.ai/artifact/NDrzxacSXbJWpzUcN5UKAZ

| Track | Scope | Owner |
|---|---|---|
| Training vertical | Engine (LLaMA-Factory), runner, generated configs, eval, watchtower runs | Manan + Ved (joint) |
| Platform surface | `/v1/fine_tuning/*` API, schema, dataset validator, dashboard UI, adapter serving | Manan + Ved (joint) |

Joint ownership only works if every task on the board names who is doing it
this week. G4 was raised because the project stalled with nobody owning
anything; if work stalls again, split the tracks.

## The seam

Serving belongs to the platform surface, but it consumes the training
vertical's artifact. The interface between the two is the runner protocol and
the adapter artifact in [`finetune_contract.md`](finetune_contract.md).

A change to any of these needs **agreement from both Manan and Ved first**:

- the LoRA limits: allowed ranks, target modules, and the base-model pinning
- the `/workers/finetune-*` endpoints and their payloads
- the fields of `finetune_artifacts`

The platform fails any adapter it cannot serve. So a training-side change to
rank, modules or base model that skips this breaks jobs rather than surfacing
as a review comment.
