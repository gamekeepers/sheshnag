# Backend — control plane

FastAPI service. It authenticates users and workers, validates submitted JSONL
batches, schedules them onto workers, tracks execution, and serves results. It
owns the database; nothing else writes to it.

Runs on port 8000 by default.

**The API contract that used to live here is now
[`docs/reference/api.md`](../docs/reference/api.md)** — every endpoint, the three
credential types and where each is accepted, the batch status lifecycle and its
enforced transitions, fault tolerance, and the model catalogue rules. It is one
document rather than two so the two cannot drift.

| You want | Read |
|---|---|
| The endpoint contract | [`docs/reference/api.md`](../docs/reference/api.md) |
| To run this locally | [`docs/develop.md`](../docs/develop.md) |
| Every environment variable | [`docs/reference/configuration.md`](../docs/reference/configuration.md) |
| The database shape | [`docs/reference/data-model.md`](../docs/reference/data-model.md) |
| To deploy it | [`docs/self-host.md`](../docs/self-host.md) |

Interactive API docs, once it is running:
[http://localhost:8000/docs](http://localhost:8000/docs) (Swagger UI).
