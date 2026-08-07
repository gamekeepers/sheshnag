import pytest

@pytest.fixture(autouse=True)
def seed_test_catalog(_engine):
    from catalog_seed import _load_manifest, _MANAGED_FIELDS
    from sqlalchemy.orm import sessionmaker
    from models import ModelCatalog
    
    SM = sessionmaker(bind=_engine)
    db = SM()
    try:
        # Check if already seeded
        if db.query(ModelCatalog).count() > 0:
            return
            
        entries = _load_manifest()
        for entry in entries:
            mid = entry.get("id")
            if not mid:
                continue
            fields = {k: entry.get(k) for k in _MANAGED_FIELDS}
            fields["enabled"] = bool(entry.get("enabled", True))
            db.add(ModelCatalog(id=mid, **fields))
        db.commit()
    finally:
        db.close()

def test_list_models_contains_nomic_embed(auth_client):
    res = auth_client.get("/v1/models")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    
    nomic_model = next((m for m in data if m["id"] == "nomic-embed-text-ollama"), None)
    assert nomic_model is not None, "nomic-embed-text-ollama not found in catalog models"
    assert nomic_model["display_name"] == "nomic-embed-text"
    assert nomic_model["task_type"] == "embedding"
    assert nomic_model["vram_gb"] == 1.0
