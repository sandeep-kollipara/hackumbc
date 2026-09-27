from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import math
from types import SimpleNamespace

import httpx
from openai import OpenAI
from PIL import Image
import pytest

from enrichment.clustering import cluster
from enrichment.config import Config
from enrichment.crops import crop_objects
from enrichment.models import OpenAIModels, decode_image, normalized
from enrichment.pipeline import Pipeline
from enrichment.repository import ChromaRepository, atomic_json
from enrichment.schemas import ImageAnalysis, BoundingBox
from enrichment.source import SourceImage, S3Source


def analysis(count=1):
    return ImageAnalysis.model_validate({
        "scene_meta": {"description": "Desk", "environment": "office", "lighting": "natural"},
        "objects": [{"object_name": "cup", "object_description": "Blue cup", "object_location": "on desk",
                     "bounding_boxes": [{"x1": 250, "y1": 100, "x2": 750, "y2": 900}]} for _ in range(count)],
    })


def vector(angle=0):
    return [math.cos(angle), math.sin(angle)] + [0.0] * 766


def record(record_id, source_id="source", angle=0, username="alice"):
    return {"record_id": record_id, "source_id": source_id, "object_id": record_id,
            "username": username, "object_name": "cup", "scene_meta": {"lighting": "natural"},
            "bounding_boxes": [{"x1": 0, "y1": 0, "x2": 1000, "y2": 1000}],
            "embedding": vector(angle), "text_embedding": vector(angle)}


@pytest.fixture
def config(tmp_path):
    return Config(bucket="test-bucket", data_dir=tmp_path)


@pytest.fixture
def source_image():
    return SourceImage("test-bucket", "2026-09-27_12-30-01-000123_alice_test.jpeg", '"etag"',
                       1, datetime(2026, 9, 27, tzinfo=timezone.utc))


def test_actual_dimensions_and_duplicate_names(tmp_path):
    crops = list(crop_objects(Image.new("RGB", (640, 480)), analysis(2), tmp_path))
    assert [crop.image.size for crop in crops] == [(320, 384), (320, 384)]
    assert crops[0].path != crops[1].path
    assert crops[0].metadata["crop_pixel_box"] == [160, 48, 480, 432]
    assert crops[0].metadata["bounding_boxes"][0]["x1"] == 250


@pytest.mark.parametrize("box", [(-1, 0, 10, 10), (0, 0, 1001, 10), (10, 0, 10, 10), (0, 20, 10, 10)])
def test_invalid_boxes(box):
    with pytest.raises(ValueError):
        BoundingBox(**dict(zip(("x1", "y1", "x2", "y2"), box)))


def test_exif_orientation():
    buffer = BytesIO()
    original = Image.new("RGB", (640, 480))
    exif = original.getexif()
    exif[274] = 6
    original.save(buffer, format="JPEG", exif=exif)
    assert decode_image(buffer.getvalue()).size == (480, 640)


def test_browser_metadata_and_path_safety(source_image):
    metadata = source_image.metadata()
    assert metadata["username"] == "alice_test"
    assert metadata["timestamp"] == "2026-09-27T12:30:01.000123+00:00"
    assert "run_id" not in metadata
    unsafe = replace(source_image, key="../../image.jpeg")
    assert "/" not in unsafe.source_id
    assert unsafe.source_id != source_image.source_id


def test_s3_pagination_download_and_size_limits(config, source_image):
    calls = []
    body = BytesIO(b"x")
    class Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return self
        def paginate(self, **kwargs):
            calls.append(kwargs)
            yield {"Contents": [{"Key": "image.JPEG", "ETag": '"etag"', "Size": 1,
                                  "LastModified": source_image.modified}]}
            yield {}
            yield {"Contents": [{"Key": "note.txt"}]}
        def get_object(self, **kwargs):
            calls.append(kwargs)
            return {"Body": body}
    source = S3Source(config, Client())
    assert len(list(source.images())) == 1
    assert source.download(source_image) == b"x"
    assert calls[-1]["IfMatch"] == source_image.etag
    assert body.closed
    with pytest.raises(ValueError):
        source.download(replace(source_image, size=config.max_image_bytes + 1))


def test_real_chroma_indexes_roundtrip_search_replace(config):
    repository = ChromaRepository(config)
    repository.replace_source("source", [record("one"), record("two", angle=1)])
    assert repository.contains_source("source", ["one", "two"])
    document = repository.get_object("one")
    assert document["scene_meta"] == {"lighting": "natural"}
    assert len(document["embedding"]) == len(document["text_embedding"]) == 768
    assert [r["record_id"] for r in repository.search(vector(), threshold=0.15)] == ["one"]
    assert repository.search(vector(), "text")[0]["record_id"] == "one"
    assert repository.search(vector(), where={"username": "bob"}) == []
    repository.replace_source("source", [record("three")])
    assert repository.contains_source("source", ["three"])
    assert ChromaRepository(config).get_object("three")["record_id"] == "three"
    with pytest.raises(ValueError, match="Incompatible"):
        ChromaRepository(replace(config, text_model="text-embedding-3-large"))


def test_journal_repairs_partial_two_index_write(config):
    repository = ChromaRepository(config)
    records = [record("one")]
    atomic_json(repository.journal / "source.json", {"source_id": "source", "records": records})
    recovered = ChromaRepository(config)
    assert recovered.contains_source("source", ["one"])
    assert not list(repository.journal.glob("*.json"))
    recovered.replace_source("source", [])
    assert recovered.contains_source("source", [])


def test_failure_between_indexes_is_recovered(config, monkeypatch):
    repository = ChromaRepository(config)
    collection_type = type(repository.text)
    original = collection_type.upsert
    def fail_text(self, **kwargs):
        if self.name.endswith("_text"):
            raise RuntimeError("Interrupted second write")
        return original(self, **kwargs)
    monkeypatch.setattr(collection_type, "upsert", fail_text)
    with pytest.raises(RuntimeError, match="Interrupted"):
        repository.replace_source("source", [record("one")])
    assert repository.image.count() == 1
    assert repository.text.count() == 0
    monkeypatch.setattr(collection_type, "upsert", original)
    assert ChromaRepository(config).contains_source("source", ["one"])


def test_transitive_union_find_and_user_isolation(config):
    repository = ChromaRepository(config)
    # A-B and B-C are close, A-C is not: all three must share a component.
    records = [record("a", angle=0), record("b", angle=0.4), record("c", angle=0.8),
               record("d", angle=0, username="bob")]
    repository.replace_source("source", records)
    assert cluster(repository) == {"records": 4, "objects": 2}
    ids = [repository.get_object(x)["object_id"] for x in "abcd"]
    assert ids[0] == ids[1] == ids[2] != ids[3]
    assert cluster(repository, across_users=True)["objects"] == 1
    assert repository.search(vector(), "text")[0]["object_id"] == repository.get_object("a")["object_id"]


class FakeModels:
    calls = 0
    fail_embedding = False
    def analyze(self, image):
        self.calls += 1
        return analysis(2)
    def embed_texts(self, texts):
        if self.fail_embedding:
            raise RuntimeError("Temporary failure")
        return [vector() for _ in texts]


class FakeDino:
    def embed_images(self, images):
        return [vector() for _ in images]


def pipeline(config, source_image):
    buffer = BytesIO()
    Image.new("RGB", (640, 480)).save(buffer, format="PNG")
    source = SimpleNamespace(download=lambda _: buffer.getvalue(), images=lambda: iter([source_image]))
    models = FakeModels()
    return Pipeline(config, source, models, FakeDino(), ChromaRepository(config))


def test_pipeline_resume_changed_source_and_missing_index(config, source_image):
    job = pipeline(config, source_image)
    assert job.run() == {"processed": 1, "skipped": 0, "failed": 0}
    assert job.process(source_image) == "skipped"
    assert job.models.calls == 1
    text_ids = job.repository.text.get()["ids"]
    stored = job.repository.get_object(text_ids[0])
    assert "is_gaze_target" not in stored
    assert "run_id" not in stored
    job.repository.text.delete(ids=text_ids)
    assert job.process(source_image) == "processed"
    assert job.models.calls == 1
    assert job.process(replace(source_image, etag='"new"')) == "processed"
    assert job.models.calls == 2
    assert job.repository.image.count() == job.repository.text.count() == 2


def test_analysis_cached_after_embedding_failure(config, source_image):
    job = pipeline(config, source_image)
    job.models.fail_embedding = True
    assert job.run()["failed"] == 1
    assert job.repository.image.count() == 0
    job.models.fail_embedding = False
    assert job.run()["processed"] == 1
    assert job.models.calls == 1


def test_empty_scene_removes_obsolete_records_and_resumes(config, source_image):
    job = pipeline(config, source_image)
    job.process(source_image)
    job.models.analyze = lambda image: analysis(0)
    job.process(source_image, force=True)
    assert job.repository.image.count() == job.repository.text.count() == 0
    assert job.process(source_image) == "skipped"


def test_forced_refresh_failure_does_not_skip_retry(config, source_image):
    job = pipeline(config, source_image)
    job.process(source_image)
    job.models.fail_embedding = True
    with pytest.raises(RuntimeError):
        job.process(source_image, force=True)
    job.models.fail_embedding = False
    assert job.process(source_image) == "processed"
    assert job.models.calls == 2


def test_failed_forced_analysis_discards_previous_cache(config, source_image):
    job = pipeline(config, source_image)
    job.process(source_image)
    original = job.models.analyze
    def fail(image):
        raise RuntimeError("Analysis unavailable")
    job.models.analyze = fail
    with pytest.raises(RuntimeError):
        job.process(source_image, force=True)
    job.models.analyze = original
    assert job.process(source_image) == "processed"
    assert job.models.calls == 2


def test_actual_openai_sdk_structured_response_and_embeddings(config):
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"object": "list", "model": config.text_model,
                "data": [{"object": "embedding", "index": i, "embedding": vector()} for i in range(2)],
                "usage": {"prompt_tokens": 2, "total_tokens": 2}})
        return httpx.Response(200, json={"id": "resp_test", "object": "response", "created_at": 0,
            "model": config.vision_model, "status": "completed", "parallel_tool_calls": False,
            "tool_choice": "auto", "tools": [],
            "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": analysis().model_dump_json(), "annotations": []}]}]})
    client = OpenAI(api_key="test-only", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    models = OpenAIModels(config, client)
    assert models.analyze(Image.new("RGB", (10, 10))) == analysis()
    assert len(models.embed_texts(["cup", "chair"])) == 2
    assert requests[0]["model"] == "gpt-6-luna"
    assert requests[0]["text"]["format"]["strict"] is True
    assert "is_gaze_target" not in json.dumps(requests[0])
    assert requests[1]["dimensions"] == 768


@pytest.mark.parametrize("values", [[0, 0], [float("nan"), 0], [1]])
def test_invalid_vectors(values):
    with pytest.raises(ValueError):
        normalized(values, 2)


def test_refused_or_incomplete_analysis(config):
    client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **_: SimpleNamespace(status="incomplete", output_parsed=None)))
    with pytest.raises(RuntimeError, match="incomplete or refused"):
        OpenAIModels(config, client).analyze(Image.new("RGB", (10, 10)))
