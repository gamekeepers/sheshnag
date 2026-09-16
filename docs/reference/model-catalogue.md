# Model catalogue

*Last updated 2026-09-15 (registry schema: serving profiles, artifact files, capabilities/lineage, naming rules — #116).*

The set of models a user may select for a batch. `body.model` in a submitted
JSONL is a **catalogue id** — a stable platform slug — not a raw runtime tag.

## Why a catalogue (design)

- **Identity is a pinned artifact.** Each `model_catalog` row is one concrete
  runnable thing: weights + quantization + runtime. A quantized Ollama GGUF
  (`mistral:7b`, Q4_K_M) and an fp16 HF model are **different entries**, never
  merged. A batch bound to an id therefore never silently changes precision or
  runtime — reproducibility. (Guarantee is *artifact* reproducibility, not
  bit-exact; GPU inference is inherently nondeterministic.)
- **The runtime string is internal.** The user-facing id is a slug; the exact
  `mistral:7b` / HF repo id lives only in `runtime_model_id`. This decouples
  the public API from runtime naming (retagging doesn't break stored batches)
  and resolves runtime naming by **lookup, not translation** — poll hands the
  daemon the exact `runtime_model_id` to run.
- **Identity is curated; availability is derived.** The catalogue is a curated
  list (seed manifest / admin), *not* the union of what workers registered —
  Ollama tags float (two workers can pull different digests under `mistral:7b`)
  and worker metadata is untrusted. Worker registrations/heartbeats feed
  **availability** (which entries are servable, matched by digest), never
  identity.

## Schema (`model_catalog`)

| column | meaning |
| --- | --- |
| `id` | stable platform slug — what the user puts in `body.model` |
| `display_name` | human label for the picker |
| `runtime` | **deprecated** — moving to `serving_profiles`; kept dual-written for existing readers |
| `runtime_model_id` | **deprecated** — moving to `serving_profiles`; kept dual-written |
| `digest` | reproducibility pin + availability join key (may be null → name-matched) |
| `quantization`, `parameter_size`, `context_length` | descriptive (curated once) |
| `vram_gb` | scheduling requirement (VRAM to run) — **not derivable from Ollama** |
| `size_gb` | on-disk size (feeds download size cap) |
| `task_type` | `chat` \| `text-generation` \| `embedding` \| `vision` |
| `capabilities` | JSON — what the MODEL can do (`json_mode`, `vision`, `embeddings`, `logprobs`); runtime mechanism differences live in the executor's `capabilities()`, effective = model AND runtime |
| `lineage` | upstream base weights (HF repo id) — groups quants of the same model; organizational only, never identity; NULL = ungrouped |
| `source_type` / `source_ref` / `source_revision` / `homepage_url` | provenance (where it came from / model card) — never a matching key |
| `org_id` | NULL = public; set = org-private (reserved for tier 2) |
| `status` | `active` \| `requested` \| `deprecated` \| `unverified` |
| `enabled` | false hides it from scheduling and `GET /v1/models` (staging) |

Descriptive metadata lives here, curated once — **not** replicated on the
per-worker `runtime_models` rows, which stay lean (name, `runtime_model_id`,
`digest`, `loaded`, `status`) for scheduling.

## Child tables

**`serving_profiles`** — how one artifact is served by one runtime:
`(catalog_id, runtime, runtime_model_id, params)`, unique per
`(catalog_id, runtime)`. `params` are platform-owned server-launch knobs
(llama.cpp `n_ctx`/`parallel`, vLLM `max_model_len`) — per-request sampling
params still travel in each batch row's `body`. Entries without an explicit
`profiles:` list in the manifest get one profile derived from the legacy
`runtime`/`runtime_model_id` pair. Adding a runtime = a new profile row,
never a new catalogue entry.

**`catalog_artifact_files`** — the files an artifact comprises, each with its
own sha256: a vision GGUF is weights + mmproj, a safetensors model is many
shards. Provisioning must verify every row before the artifact counts as
present. Single-file entries may skip this table (`digest` suffices).

## Naming rules (enforced by the seed)

`id` is lowercase `[a-z0-9]` segments joined by `-`; it must **end with the
quant slug** when the entry declares a `quantization` (`Q4_K_M` → `-q4km`),
and must **never contain a runtime name** (`ollama`, `vllm`, `llamacpp`) —
the slug has to survive a runtime swap unchanged. Violations are logged and
the entry is skipped, not a boot failure.

Renames are one-shot: give the entry its new `id` plus
`renamed_from: <old-id>`; the seed updates the row (and its child rows) in
place. There is no aliases table — `batches.model` is a plain string, so
historical rows keep the retired slug.

## Scheduling (`provider_picker.py`)

At `POST /workers/poll`, for each `validated` batch the picker:

1. resolves `batch.model` → catalogue entry (skips if not found);
2. requires the worker to fit `vram_gb` (when the worker has heartbeated);
3. requires the worker to **host** `runtime_model_id` (advertised at
   registration / reported loaded), enforcing **digest equality when both the
   entry and the worker's model carry a digest** — *same tag + different digest
   ⇒ not matched*; falls back to name match when either digest is absent;
4. prefers a worker already serving the model (loaded in VRAM).

Poll returns `runtime_model_id` (not the slug) as the job's `model`, so the
daemon runs the exact runtime string. Batch validation rejects a `body.model`
not in the catalogue (`unsupported_model`).

`GET /v1/models` lists selectable entries (public + the caller's org) with
their descriptive + provenance fields, so users know valid ids.

## Reconciliation — worker rows vs catalogue pins

Every artifact a worker reports lands in one state on its `runtime_models`
row (`backend/reconciliation.py`); the picker routes **only** to `available`:

| State | Meaning | How it clears |
| --- | --- | --- |
| `available` | hash matches a pin (`catalog_id` set) — or the row has **no hash** and name-matches (old daemon, vLLM; `catalog_id` null) | — |
| `unregistered` | hash matches no entry, name matches no entry | admin **adopts** it (`POST /v1/models/adopt`) or the blob is replaced |
| `drift` | name claims a pinned entry, bytes differ from every pin under that name | re-provision the worker, or add the quant as a **new** entry |
| `missing` | dropped from a full inventory (`ollama rm` on the box) | reappears in a later inventory → re-classified |

Rows without a hash are never quarantined: there is nothing to adopt and the
picker already requires a catalogue entry for the name. A hash-bearing row
registered before its entry existed self-heals on the next heartbeat
(re-classified every beat). Adopted entries carry `status: unverified` —
selectable and schedulable like `active`, provenance unconfirmed.

## Curation workflow

Source of truth: `backend/catalog/models.yaml` (version-controlled). At
startup `catalog_seed.py` **upserts** it — inserts new ids, updates managed
fields on existing ids, and leaves entries *not* in the manifest untouched
(admin / org-private stay). `enabled: false` entries are ignored by the
scheduler and `GET /v1/models`.

### 1. Add / edit models

Edit `models.yaml` directly, or discover from a live Ollama:

```bash
# Enrich entries already in the manifest with real digest + metadata
python -m scripts.capture_catalog --ollama http://localhost:11434 \
    --manifest backend/catalog/models.yaml [--only mistral:7b llama3:8b]

# Discover: append STAGED stubs (enabled:false) for Ollama models not yet
# catalogued, with everything Ollama can derive pre-filled
python -m scripts.capture_catalog --discover --ollama http://localhost:11434 \
    --manifest backend/catalog/models.yaml
```

Discovery is an **allow-list**: stubs land `enabled: false`, so nothing
becomes selectable until you review it, set/confirm `vram_gb` + `display_name`,
tidy the `id`, and flip `enabled: true`.

### 2. `digest` — the reproducibility pin

The artifact **file's** sha256 — for Ollama, the manifest's model-layer
digest, which is exactly what daemons report in their inventory (#116).
It is **not** `/api/tags`' `digest`: that hashes the manifest file itself and
matches nothing a worker sends, so a catalogue pinned to it silently starves
every digest-pinned model (the picker logs `Digest mismatch` once per pair).
`capture_catalog` reads the file hash from the local manifests tree
(`--models-dir`, auto-detected) and falls back to `registry.ollama.ai`'s
manifest API for models not pulled locally — no blob download needed.
Leave it null and the scheduler **name-matches** (any digest under that tag);
fill it to switch on the strict same-tag/different-digest guard.

### 3. `vram_gb` — not derivable from Ollama

`/api/tags` reports disk `size`, **not** the VRAM a model needs to run
(≈ weights + KV-cache(context) + overhead — not a fixed number). Options:

- default: `capture_catalog` fills an **estimate** from disk size (verify it);
- `--measure-vram`: loads each model and reads the real footprint from
  `/api/ps` `size_vram` (accurate, but loads each model — needs GPU headroom);
- a null `vram_gb` **disables** the VRAM filter for that model (any worker
  "fits") — set a real value before relying on capacity matching.

### 4. Load into the DB

Seeding runs at backend **startup**. Editing the YAML alone does nothing until
you reseed:

```bash
# restart the backend, OR from backend/:
../.venv/bin/python -c "from catalog_seed import seed_model_catalog; seed_model_catalog()"
```

Verify: `GET /v1/models`, or count `model_catalog` rows.

## Onboarding models not yet catalogued

There is **no "run an uncatalogued model" path** — instead, tiers of how an
entry is added (every runnable model stays a pinned entry):

1. **Platform-curated (public)** — the seed manifest. V1 default.
2. **Org-private self-service** — an org adds a pinned entry scoped to itself
   (`org_id`). Reserved; column exists, flow not wired.
3. **Request → promote** — user requests, admin promotes from discovery
   staging. Follow-up.

On-the-fly downloads only ever **materialize an existing catalogue entry**
onto a worker that lacks it — never run an arbitrary id.

## Related

- Design/decisions (vault): *Sheshnag - Batch processing via Ollama runtime*,
  *Sheshnag - Digest-based model matching*.
- Design intent for workloads and model metadata lived in the v1 spec, which is no longer published; the behaviour it described is documented above and in [API reference](api.md).
