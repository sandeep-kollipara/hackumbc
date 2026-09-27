from dataclasses import replace
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace

import chromadb
from PIL import Image
import pytest

from RAG_delivery.object_search import (
    Config, ObjectQuery, ObjectSearch, SearchError, capture_time, database_status,
    latest_matches, local_crop, open_collection, parse_query,
)


def record(key, group, timestamp, vector, name="cup", user="alice"):
    data = {"record_id": key, "object_id": group, "username": user,
            "timestamp": timestamp, "object_name": name, "object_description": "Blue ceramic cup",
            "object_location": "On the desk next to a laptop", "scene_meta": {
                "environment": "office", "description": "A wooden desk by a window", "lighting": "daylight"},
            "parent_image": "s3://test/image.jpeg"}
    return key, vector, json.dumps(data)


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path, text_dimensions=2)


def create_database(config, records):
    client = chromadb.PersistentClient(path=str(config.data_dir / "chroma"))
    collection = client.create_collection(config.collection + "_text", embedding_function=None,
        metadata={"embedding_model": config.text_model, "dimensions": config.text_dimensions, "schema_version": 1},
        configuration={"hnsw": {"space": "cosine"}})
    if records:
        collection.add(ids=[r[0] for r in records], embeddings=[r[1] for r in records],
                       documents=[r[2] for r in records],
                       metadatas=[{"username": json.loads(r[2])["username"]} for r in records])
    return collection


def test_latest_is_not_nearest_and_multiple_instances():
    records = [record("old", "a", "2026-09-01T10:00:00Z", [1, 0]),
               record("new", "a", "2026-09-02T10:00:00Z", [0, 1], name="mug"),
               record("other", "b", "2026-09-03T10:00:00Z", [1, 0]),
               record("keys", "k", "2026-09-04T10:00:00Z", [0, 1], name="keys")]
    result = latest_matches(records, ["cup", "keys"], [[1, 0], [0, 1]], threshold=0.1)
    cups, keys = result["groups"]
    assert [x["record_id"] for x in cups["results"]] == ["other", "new"]
    assert cups["results"][1]["matched_label"] == "cup"
    assert cups["results"][1]["scene_meta"]["environment"] == "office"
    assert keys["results"][0]["record_id"] == "keys"


def test_timezone_order_and_user_isolation():
    records = [record("utc", "a", "2026-09-02T12:00:00Z", [1, 0]),
               record("offset", "a", "2026-09-02T09:00:00-04:00", [1, 0]),
               record("bob", "a", "2026-09-03T10:00:00Z", [1, 0], user="bob")]
    result = latest_matches(records, ["cup"], [[1, 0]], username="alice")
    assert [r["record_id"] for r in result["groups"][0]["results"]] == ["offset"]
    assert latest_matches(records, ["cup"], [[1, 0]])["groups"][0]["total_instances"] == 2


def test_bad_records_and_no_matches():
    records = [record("badtime", "a", "not-a-date", [1, 0]),
               record("badvector", "b", "2026-09-02T10:00:00Z", [0, 0]),
               record("other", "c", "2026-09-03T10:00:00Z", [0, 1])]
    result = latest_matches(records, ["cup"], [[1, 0]], threshold=0.1)
    assert result["invalid_records"] == 2
    assert result["groups"][0]["results"] == []
    assert capture_time("2026-09-01T10:00:00") is None


def test_full_scan_does_not_truncate_recent_matches(config):
    records = [record(str(i), "a", f"2026-09-01T10:{i // 60:02d}:{i % 60:02d}Z", [1, 0]) for i in range(300)]
    collection = create_database(config, records)
    from RAG_delivery.object_search import rows
    result = latest_matches(rows(collection), ["cup"], [[1, 0]], limit=1)
    assert result["scanned"] == 300
    assert result["groups"][0]["results"][0]["record_id"] == "299"


class FakeClient:
    def __init__(self, names):
        self.names = names
        self.responses = SimpleNamespace(parse=self.parse)
        self.embeddings = SimpleNamespace(create=self.embed)
        self.calls = []
    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status="completed", output_parsed=ObjectQuery(objects=self.names))
    def embed(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1, 0] if name == "cup" else [0, 1])
                                     for i, name in enumerate(kwargs["input"])])


def test_service_multiple_objects_uses_same_model_and_does_not_mutate(config):
    records = [record("cup", "a", "2026-09-01T10:00:00Z", [1, 0]),
               record("keys", "b", "2026-09-02T10:00:00Z", [0, 1], name="keys")]
    collection = create_database(config, records)
    before = collection.get(include=["documents", "metadatas"])
    client = FakeClient(["cup", "keys"])
    result = ObjectSearch(config, client).search("Where are my cup and keys?", "alice")
    assert len(result["groups"]) == 2
    assert [g["results"][0]["record_id"] for g in result["groups"]] == ["cup", "keys"]
    assert client.calls[1]["dimensions"] == 2
    assert client.calls[1]["model"] == config.text_model
    assert collection.get(include=["documents", "metadatas"]) == before
    assert database_status(config) == {"records": 2, "users": ["alice"]}


def test_query_parser_deduplicates_and_validates():
    assert parse_query(FakeClient([" cup ", "CUP", "keys"]), "Where are they?") == ["cup", "keys"]
    with pytest.raises(SearchError):
        parse_query(FakeClient([]), " " * 2)
    client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **_: SimpleNamespace(status="incomplete", output_parsed=None)))
    with pytest.raises(SearchError):
        parse_query(client, "Where is my cup?")


def test_missing_database_does_not_create_it_or_call_api(config):
    client = FakeClient(["cup"])
    with pytest.raises(SearchError, match="No enrichment"):
        ObjectSearch(config, client).search("cup")
    assert not (config.data_dir / "chroma").exists()
    assert client.calls == []


def test_empty_database_no_api_calls(config):
    create_database(config, [])
    client = FakeClient(["cup"])
    assert "No objects" in ObjectSearch(config, client).search("cup")["message"]
    assert client.calls == []


def test_busy_pending_and_incompatible_database(config):
    create_database(config, [])
    with (config.data_dir / ".workflow.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SearchError, match="updating"):
            with open_collection(config):
                pass
    with pytest.raises(SearchError, match="differ"):
        with open_collection(replace(config, text_dimensions=3)):
            pass
    journal = config.data_dir / "journal" / config.collection
    journal.mkdir(parents=True)
    (journal / "pending.json").write_text("{}")
    with pytest.raises(SearchError, match="incomplete"):
        with open_collection(config):
            pass


def test_crop_path_restriction(config, tmp_path):
    artifact = config.data_dir / "artifacts"
    artifact.mkdir()
    image = artifact / "crop.png"
    Image.new("RGB", (10, 10)).save(image)
    assert local_crop({"crop_path": str(image)}, config) == image
    outside = tmp_path / "outside.png"
    Image.new("RGB", (10, 10)).save(outside)
    assert local_crop({"crop_path": str(outside)}, config) is None
    (artifact / "link.png").symlink_to(outside)
    assert local_crop({"crop_path": str(artifact / "link.png")}, config) is None


def test_panel_cards_and_dash_routes(config):
    from result_view import render_results
    from chatbot_viewer import create_app, search_response
    records = [record("one", "a", "2026-09-01T10:00:00Z", [1, 0], name="<script>alert(1)</script>")]
    create_database(config, records)
    payload = latest_matches(records, ["cup"], [[1, 0]])
    page = render_results(payload, config)
    assert "office" in page
    assert "LAST SEEN" in page
    assert "<script>alert(1)</script>" not in page
    app = create_app(config)
    web = app.server.test_client()
    assert web.get("/").status_code == 200
    assert web.get("/_dash-layout").status_code == 200
    assert web.get("/health").data == b"ok"
    service = ObjectSearch(config, FakeClient(["cup"]))
    page, status = search_response(config, "Where is my cup?", '"alice"', 3, 0.35, service)
    assert "1 latest sightings" in status
    assert "office" in page

