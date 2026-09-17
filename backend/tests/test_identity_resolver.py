"""Identity resolver (#116 follow-up): confirm a quarantined hash against
its public source. No database — every registry call goes through an
`httpx.MockTransport`, and the resolver's clock is injected so the negative
cache TTL is tested without sleeping.
"""
import json

import httpx
import pytest

from identity_resolver import (
    Confirmed,
    IdentityResolver,
    Unconfirmed,
    model_layer_digest,
    parse_local_name,
    registry_file_digest,
)

SHA = "7cd4618c1faf8b7233c6c906dac1694b6a47684b37b8895d470ac688520b9c01"
OTHER = "1" * 64
HF_REV = "0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3"


def ollama_manifest(model_digest=SHA):
    """Shape of registry.ollama.ai's manifest API (captured 2026-09-16)."""
    return {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                   "digest": "sha256:" + "c" * 64, "size": 492},
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model",
             "digest": f"sha256:{model_digest}", "size": 815310432},
            {"mediaType": "application/vnd.ollama.image.template",
             "digest": "sha256:" + "e" * 64, "size": 358},
            {"mediaType": "application/vnd.ollama.image.license",
             "digest": "sha256:" + "d" * 64, "size": 8432},
        ],
    }


def hf_tree(file_digest=SHA):
    """Shape of huggingface.co/api/models/{repo}/tree/{rev} (captured 2026-09-16)."""
    return [
        {"type": "file", "oid": "b051f4c2", "size": 2842, "path": ".gitattributes"},
        {"type": "file", "oid": "a50e6536", "size": 657289344,
         "lfs": {"oid": file_digest, "size": 657289344, "pointerSize": 134},
         "path": "Llama-3.2-1B-Instruct-IQ3_M.gguf"},
        {"type": "file", "oid": "49508f29", "size": 743141504,
         "lfs": {"oid": "6" * 64, "size": 743141504, "pointerSize": 134},
         "path": "Llama-3.2-1B-Instruct-Q4_K_M.gguf"},
    ]


class Registry:
    """Scripted responses keyed by URL path; records every request."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # A key with a query string matches that exact page; a bare path
        # matches every page of it.
        query = request.url.query.decode()
        specific = request.url.path + (f"?{query}" if query else "")
        route = self.routes.get(specific, self.routes.get(request.url.path))
        if route is None:
            return httpx.Response(404, json={"errors": [{"code": "MANIFEST_UNKNOWN"}]})
        if isinstance(route, Exception):
            raise route
        status, body = route[0], route[1]
        headers = route[2] if len(route) > 2 else None
        return httpx.Response(status, json=body, headers=headers)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


def make_resolver(registry, clock, ttl=60):
    return IdentityResolver(client=registry.client(), negative_ttl=ttl, clock=clock)


# ─── name parsing ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("gemma3:1b", ("library", "gemma3", "1b")),
    ("gemma3", ("library", "gemma3", "latest")),
    ("qwen2.5:7b-instruct-q4_K_M", ("library", "qwen2.5", "7b-instruct-q4_K_M")),
    ("someuser/mymodel:v2", ("someuser", "mymodel", "v2")),
    ("registry.ollama.ai/library/gemma3:1b", ("library", "gemma3", "1b")),
])
def test_parse_ollama_names(name, expected):
    p = parse_local_name(name)
    assert (p.namespace, p.model, p.tag) == expected


def test_parse_hf_names():
    p = parse_local_name("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M")
    assert (p.user, p.repo, p.tag) == ("bartowski", "Llama-3.2-1B-Instruct-GGUF", "Q4_K_M")
    assert parse_local_name("huggingface.co/u/r").repo_id == "u/r"


@pytest.mark.parametrize("name", ["some.host/ns/model:tag", "a/b/c:d", "hf.co/onlyuser", "", None])
def test_parse_rejects_unsupported(name):
    assert parse_local_name(name) is None


# ─── ollama registry ─────────────────────────────────────────────────────────

def test_library_hash_confirmed(clock):
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, ollama_manifest())})
    res = make_resolver(reg, clock).resolve("gemma3:1b", SHA)

    assert res == Confirmed(source_type="ollama-library", source_ref="gemma3",
                            source_revision=None, digest=SHA,
                            homepage_url="https://ollama.com/library/gemma3")
    (req,) = reg.requests
    assert req.url.host == "registry.ollama.ai"
    assert req.headers["accept"] == "application/vnd.docker.distribution.manifest.v2+json"


def test_community_name_uses_its_namespace(clock):
    reg = Registry({"/v2/someuser/mymodel/manifests/latest": (200, ollama_manifest())})
    res = make_resolver(reg, clock).resolve("someuser/mymodel", SHA)

    assert isinstance(res, Confirmed)
    assert res.source_ref == "someuser/mymodel"
    assert res.homepage_url == "https://ollama.com/someuser/mymodel"


def test_hash_prefix_and_case_are_normalised(clock):
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, ollama_manifest())})
    res = make_resolver(reg, clock).resolve("gemma3:1b", "sha256:" + SHA.upper())
    assert isinstance(res, Confirmed) and res.digest == SHA


def test_mismatch_stays_unconfirmed(clock):
    """The name exists publicly but serves different bytes: a re-quantised or
    tampered file must not be adopted under the public name."""
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, ollama_manifest(OTHER))})
    assert make_resolver(reg, clock).resolve("gemma3:1b", SHA) == Unconfirmed("digest-mismatch")


def test_only_model_layers_count(clock):
    """A template/license layer whose digest happens to equal the hash is
    not the weights."""
    manifest = ollama_manifest(OTHER)
    manifest["layers"][1]["digest"] = f"sha256:{SHA}"
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, manifest)})
    assert make_resolver(reg, clock).resolve("gemma3:1b", SHA) == Unconfirmed("digest-mismatch")


def test_unknown_tag_is_unconfirmed(clock):
    reg = Registry()
    assert make_resolver(reg, clock).resolve("gemma3:nope", SHA) == Unconfirmed("http-404")


def test_unsupported_host_makes_no_request(clock):
    reg = Registry()
    res = make_resolver(reg, clock).resolve("some.host/ns/model:tag", SHA)
    assert res == Unconfirmed("unsupported-registry")
    assert reg.requests == []


def test_missing_hash_makes_no_request(clock):
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, ollama_manifest())})
    assert make_resolver(reg, clock).resolve("gemma3:1b", None) == Unconfirmed("no-hash")
    assert reg.requests == []


# ─── caching ─────────────────────────────────────────────────────────────────

def test_positive_result_is_final(clock):
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, ollama_manifest())})
    r = make_resolver(reg, clock)
    first = r.resolve("gemma3:1b", SHA)
    clock.t += 10 ** 6
    assert r.resolve("gemma3:1b", SHA) == first
    assert len(reg.requests) == 1


def test_network_error_is_cached_for_ttl_then_retried(clock):
    """A flaky registry is asked once per TTL per (name, hash), not once per
    sweep — and a recovered registry is noticed after the TTL."""
    reg = Registry({"/v2/library/gemma3/manifests/1b": httpx.ConnectError("boom")})
    r = make_resolver(reg, clock, ttl=60)

    res = r.resolve("gemma3:1b", SHA)
    assert res == Unconfirmed("network-error: ConnectError")
    clock.t += 30
    assert r.resolve("gemma3:1b", SHA) == res
    assert len(reg.requests) == 1

    reg.routes["/v2/library/gemma3/manifests/1b"] = (200, ollama_manifest())
    clock.t += 31
    assert isinstance(r.resolve("gemma3:1b", SHA), Confirmed)
    assert len(reg.requests) == 2


def test_negative_cache_is_per_name_and_hash(clock):
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, ollama_manifest(OTHER))})
    r = make_resolver(reg, clock)
    r.resolve("gemma3:1b", SHA)
    r.resolve("gemma3:1b", OTHER)     # same name, the hash the registry serves
    assert len(reg.requests) == 2
    assert isinstance(r.resolve("gemma3:1b", OTHER), Confirmed)


def test_same_source_under_two_names_shares_one_request(clock):
    """The auto-adopt loop asks about one hash under several served names
    (alias, repo id): they resolve the same upstream repo, so the request
    is made once per TTL, not once per name."""
    reg = Registry({HF_INFO: (404, {"error": "Repository not found"})})
    r = make_resolver(reg, clock, ttl=60)
    ref = "bartowski/Llama-3.2-1B-Instruct-GGUF"
    res1 = r.resolve("Llama-3.2-1B", SHA, source_ref=ref)
    res2 = r.resolve("Llama-3.2-1B-Instruct-GGUF", SHA, source_ref=ref)
    assert res1 == res2 == Unconfirmed("http-404")
    assert len(reg.requests) == 1
    clock.t += 61
    r.resolve("Llama-3.2-1B", SHA, source_ref=ref)
    r.resolve("Llama-3.2-1B-Instruct-GGUF", SHA, source_ref=ref)
    assert len(reg.requests) == 2


# ─── hugging face ────────────────────────────────────────────────────────────

HF_INFO = "/api/models/bartowski/Llama-3.2-1B-Instruct-GGUF"
HF_TREE = f"{HF_INFO}/tree/{HF_REV}"


def test_hf_hash_confirmed_at_pinned_revision(clock):
    reg = Registry({HF_INFO: (200, {"id": "bartowski/Llama-3.2-1B-Instruct-GGUF", "sha": HF_REV}),
                    HF_TREE: (200, hf_tree())})
    res = make_resolver(reg, clock).resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:IQ3_M", SHA)

    assert res == Confirmed(source_type="huggingface",
                            source_ref="bartowski/Llama-3.2-1B-Instruct-GGUF",
                            source_revision=HF_REV, digest=SHA,
                            homepage_url="https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF",
                            source_file="Llama-3.2-1B-Instruct-IQ3_M.gguf")
    assert [q.url.host for q in reg.requests] == ["huggingface.co", "huggingface.co"]
    assert reg.requests[1].url.params["recursive"] == "true"


def test_hf_tag_does_not_matter_only_bytes_do(clock):
    """Ollama's `:Q4_K_M` tag names a quant, not a file; the hash is matched
    against every LFS file in the repo — and the file that actually matched
    is recorded, so a tag that lies (here: bytes are the IQ3_M file) still
    yields a complete, re-pullable reference for the adopting entry."""
    reg = Registry({HF_INFO: (200, {"sha": HF_REV}), HF_TREE: (200, hf_tree())})
    res = make_resolver(reg, clock).resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M", SHA)
    assert isinstance(res, Confirmed)
    assert res.source_file == "Llama-3.2-1B-Instruct-IQ3_M.gguf"
    assert res.source_revision == HF_REV


def test_close_only_releases_an_owned_client(clock):
    """An injected client belongs to the caller: close() must not shut it."""
    reg = Registry({HF_INFO: (200, {"sha": HF_REV}), HF_TREE: (200, hf_tree())})
    resolver = make_resolver(reg, clock)
    resolver.close()
    # Still usable: the shared client was left open.
    assert isinstance(
        resolver.resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M", SHA), Confirmed
    )
    owned = IdentityResolver(clock=clock)
    owned.close()
    assert owned._client.is_closed


def test_hf_no_matching_file_is_unconfirmed(clock):
    reg = Registry({HF_INFO: (200, {"sha": HF_REV}), HF_TREE: (200, hf_tree(OTHER))})
    res = make_resolver(reg, clock).resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M", SHA)
    assert res == Unconfirmed("digest-mismatch")


def test_hf_adapter_repo_is_never_confirmed(clock):
    """A repo whose tree holds adapter_config.json is a PEFT/LoRA adapter,
    not a model: its weight files may hash-match, but confirmation would
    adopt the adapter as the base model — the tree is already fetched, so
    the check is free."""
    tree = hf_tree() + [{"type": "file", "oid": "f" * 40, "size": 1234,
                         "path": "adapter_config.json"}]
    reg = Registry({HF_INFO: (200, {"sha": HF_REV}), HF_TREE: (200, tree)})
    res = make_resolver(reg, clock).resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M", SHA)
    assert res == Unconfirmed("lora-adapter")


def test_hf_tree_is_paginated_until_every_shard_seen(clock):
    """HF caps the tree endpoint at 1000 entries/page: a repo whose
    non-weight files fill page 1 must not digest-mismatch forever."""
    page1 = [{"type": "file", "oid": "b" * 8, "size": 1,
              "path": f"corpus/sample-{i:04d}.txt"} for i in range(1000)]
    next_url = f"https://huggingface.co{HF_TREE}?recursive=true&cursor=abc"
    reg = Registry({
        HF_INFO: (200, {"sha": HF_REV}),
        f"{HF_TREE}?recursive=true": (200, page1,
                                      {"Link": f'<{next_url}>; rel="next"'}),
        f"{HF_TREE}?recursive=true&cursor=abc": (200, hf_tree()),
    })
    res = make_resolver(reg, clock).resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:IQ3_M", SHA)
    assert isinstance(res, Confirmed)
    assert res.source_file == "Llama-3.2-1B-Instruct-IQ3_M.gguf"
    assert len(reg.requests) == 3          # repo info + page 1 + page 2
    assert reg.requests[2].url.params["cursor"] == "abc"


def test_hf_stops_following_pages_once_all_shards_seen(clock):
    """The early exit still works: a Link header on a page that already
    holds every required shard is ignored."""
    next_url = f"https://huggingface.co{HF_TREE}?recursive=true&cursor=abc"
    reg = Registry({
        HF_INFO: (200, {"sha": HF_REV}),
        f"{HF_TREE}?recursive=true": (200, hf_tree(),
                                      {"Link": f'<{next_url}>; rel="next"'}),
        f"{HF_TREE}?recursive=true&cursor=abc": (200, []),
    })
    res = make_resolver(reg, clock).resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:IQ3_M", SHA)
    assert isinstance(res, Confirmed)
    assert len(reg.requests) == 2          # page 2 never fetched


def test_hf_digest_absent_on_all_pages_stays_unconfirmed(clock):
    page1 = [{"type": "file", "oid": "b" * 8, "size": 1, "path": "corpus.txt"}]
    next_url = f"https://huggingface.co{HF_TREE}?recursive=true&cursor=abc"
    reg = Registry({
        HF_INFO: (200, {"sha": HF_REV}),
        f"{HF_TREE}?recursive=true": (200, page1,
                                      {"Link": f'<{next_url}>; rel="next"'}),
        f"{HF_TREE}?recursive=true&cursor=abc": (200, hf_tree(OTHER)),
    })
    res = make_resolver(reg, clock).resolve("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:IQ3_M", SHA)
    assert res == Unconfirmed("digest-mismatch")
    assert len(reg.requests) == 3


def test_hf_private_or_missing_repo_is_unconfirmed(clock):
    reg = Registry({"/api/models/secret/repo": (401, {"error": "Repository not found"})})
    res = make_resolver(reg, clock).resolve("hf.co/secret/repo:Q4_K_M", SHA)
    assert res == Unconfirmed("http-401")
    assert len(reg.requests) == 1


# ─── script-facing lookup (moved out of scripts/capture_catalog.py) ──────────

def test_registry_file_digest_reads_model_layer():
    reg = Registry({"/v2/library/gemma3/manifests/1b": (200, ollama_manifest())})
    assert registry_file_digest("gemma3:1b", client=reg.client()) == (SHA, None)


def test_registry_file_digest_reports_why_not():
    reg = Registry()
    digest, reason = registry_file_digest("hf.co/u/r:Q4", client=reg.client())
    assert digest is None and "non-default registry" in reason and reg.requests == []
    digest, reason = registry_file_digest("gemma3:nope", client=reg.client())
    assert digest is None and "unavailable" in reason


def test_model_layer_digest_is_bare_hex():
    assert model_layer_digest(ollama_manifest()) == SHA
    assert model_layer_digest({"layers": []}) is None


# ─── transient vs definitive negative caching ────────────────────────────────

MANIFEST = "/v2/library/gemma3/manifests/1b"


def test_rate_limit_is_retried_after_short_ttl_not_the_full_negative_ttl(clock):
    """A 429 (or 5xx / network blip) must not quarantine a confirmable hash
    for the full 15-minute negative TTL — it is remembered for
    transient_ttl only, then re-asked."""
    reg = Registry({MANIFEST: (429, {"errors": [{"code": "TOOMANYREQUESTS"}]})})
    resolver = IdentityResolver(client=reg.client(), negative_ttl=900, transient_ttl=60, clock=clock)

    assert resolver.resolve("gemma3:1b", SHA) == Unconfirmed("http-429")
    reg.routes[MANIFEST] = (200, ollama_manifest())     # registry recovers
    clock.t += 30
    assert resolver.resolve("gemma3:1b", SHA) == Unconfirmed("http-429")   # still within 60s
    clock.t += 31
    assert isinstance(resolver.resolve("gemma3:1b", SHA), Confirmed)      # re-asked after 60s
    assert len(reg.requests) == 2


def test_definitive_mismatch_keeps_the_full_negative_ttl(clock):
    reg = Registry({MANIFEST: (200, ollama_manifest(model_digest=OTHER))})
    resolver = IdentityResolver(client=reg.client(), negative_ttl=900, transient_ttl=60, clock=clock)
    assert resolver.resolve("gemma3:1b", SHA) == Unconfirmed("digest-mismatch")
    reg.routes[MANIFEST] = (200, ollama_manifest())
    clock.t += 61
    assert resolver.resolve("gemma3:1b", SHA) == Unconfirmed("digest-mismatch")  # not transient: cached
    assert len(reg.requests) == 1
