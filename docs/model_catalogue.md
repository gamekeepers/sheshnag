# Model catalogue

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
| `runtime` | `ollama` \| `vllm` |
| `runtime_model_id` | exact runtime string (internal), e.g. `mistral:7b` |
| `digest` | reproducibility pin + availability join key (may be null → name-matched) |
| `quantization`, `parameter_size`, `context_length` | descriptive (curated once) |
| `vram_gb` | scheduling requirement (VRAM to run) — **not derivable from Ollama** |
| `size_gb` | on-disk size (feeds download size cap) |
| `task_type` | `chat` \| `text-generation` \| `embedding` \| `vision` |
| `source_type` / `source_ref` / `source_revision` / `homepage_url` | provenance (where it came from / model card) — never a matching key |
| `org_id` | NULL = public; set = org-private (reserved for tier 2) |
| `status` | `active` \| `requested` \| `deprecated` |
| `enabled` | false hides it from scheduling and `GET /v1/models` (staging) |

Descriptive metadata lives here, curated once — **not** replicated on the
per-worker `runtime_models` rows, which stay lean (name, `runtime_model_id`,
`digest`, `loaded`, `status`) for scheduling.

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

Only knowable after a pull. Leave it null and the scheduler **name-matches**
(any digest under that tag). Fill it (via `capture_catalog`, which reads the
digest from a live Ollama that has the model) to switch on the strict
same-tag/different-digest guard.

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
- Spec: v1-spec.md §5 (workloads) and §8.3 (model metadata).
