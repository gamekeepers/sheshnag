"""
VRAM footprint of a GGUF artifact as a function of context (#164).

    vram(ctx) = weights_gb + alpha * kv_bytes_per_token * ctx

`weights_gb` is everything that does not grow with context: the weights
(the artifact's own size — exact, where params x bits-per-weight is a table
lookup), the sliding-window KV cache, and a fixed allowance for compute
buffers. `kv_bytes_per_token` is the f16 K+V cache of the layers that DO grow.
`alpha` is per runtime (parallel slots, KV cache type) and is applied by the
scheduler, not here.

Every input is a GGUF metadata key — the same names Ollama's /api/show
returns in `model_info` — so one function serves the seeding script, which
reads GGUF headers, and anything holding /api/show output.

Pure: no imports from the rest of the backend, so `scripts/` can use it too.
"""

GIB = 1024 ** 3

# Compute buffers. Measured 0.3-0.45 GiB on a 12B model and ~0 on a 3.8B one
# (Ollama 0.32, Metal) — one constant rather than a model of the graph.
COMPUTE_OVERHEAD_GB = 0.25

# Interleaved sliding-window architectures whose GGUF names a window but not
# which layers use it: every n-th layer is full attention. llama.cpp
# hard-codes the same patterns. An unknown architecture with a window and no
# pattern is treated as all-full — overestimating, never OOMing.
_SWA_EVERY = {"gemma2": 2, "gemma3": 6, "gptoss": 2}

# Encoders keep no KV cache between tokens.
_NO_KV_ARCHES = frozenset({"bert", "nomic-bert", "nomic-bert-moe", "jina-bert-v2"})

# The sliding-window cache llama.cpp allocates is the window plus a batch's
# worth of cells, about twice the window (2048 cells for gemma4's 1024).
_SWA_CELLS_PER_WINDOW = 2


class UnsupportedArchitecture(ValueError):
    """Metadata that the formula would get wrong in the unsafe direction."""


def _per_layer(value, layers):
    if isinstance(value, list):
        if len(value) != layers:
            raise ValueError(f"per-layer list has {len(value)} entries, model has {layers} layers")
        return value
    return [value] * layers


def vram_profile(meta: dict, artifact_bytes: int) -> tuple:
    """(weights_gb, kv_bytes_per_token) for one artifact.

    `meta` is GGUF metadata (`general.architecture`, `<arch>.block_count`,
    `<arch>.attention.*`); `artifact_bytes` is every file the runtime loads.
    """
    arch = meta["general.architecture"]

    def get(key, default=None):
        value = meta.get(f"{arch}.{key}")
        return default if value is None else value

    if get("attention.kv_lora_rank") is not None:
        # Multi-head latent attention caches a compressed vector, not K and V
        # per head; the per-head formula below would be meaningless for it.
        raise UnsupportedArchitecture(f"{arch}: latent attention (MLA)")

    layers = int(get("block_count"))
    weights_gb = artifact_bytes / GIB + COMPUTE_OVERHEAD_GB
    if arch in _NO_KV_ARCHES:
        return round(weights_gb, 2), 0

    heads = _per_layer(get("attention.head_count"), layers)
    kv_heads = _per_layer(get("attention.head_count_kv", heads), layers)
    embedding = int(get("embedding_length"))
    key = get("attention.key_length")
    if key is None:
        key = embedding // max(heads)
    value = get("attention.value_length", key)
    key_swa = get("attention.key_length_swa", key)
    value_swa = get("attention.value_length_swa", value)

    window = get("attention.sliding_window")
    pattern = get("attention.sliding_window_pattern")
    if window and isinstance(pattern, list):
        sliding = _per_layer(pattern, layers)
    elif window and arch in _SWA_EVERY:
        n = _SWA_EVERY[arch]
        sliding = [i % n != n - 1 for i in range(layers)]
    else:
        sliding = [False] * layers

    # Hybrid models (qwen35/qwen3next) interleave attention with recurrent
    # layers: only every n-th layer keeps a KV cache, the rest a fixed-size
    # state that does not grow with context.
    interval = get("full_attention_interval")
    attention = [(i + 1) % interval == 0 for i in range(layers)] if interval else [True] * layers

    f16, f32 = 2, 4
    per_token = sum(
        kv_heads[i] * (key + value) * f16
        for i in range(layers) if attention[i] and not sliding[i]
    )
    swa_bytes = sum(
        kv_heads[i] * (key_swa + value_swa) * f16
        for i in range(layers) if attention[i] and sliding[i]
    ) * _SWA_CELLS_PER_WINDOW * (window or 0)
    state, inner = get("ssm.state_size", 0), get("ssm.inner_size", 0)
    conv, groups = get("ssm.conv_kernel", 1), get("ssm.group_count", 0)
    recurrent_bytes = attention.count(False) * f32 * (
        state * inner + (conv - 1) * (inner + 2 * groups * state)
    )
    fixed_gb = (swa_bytes + recurrent_bytes) / GIB
    return round(weights_gb + fixed_gb, 2), int(per_token)
