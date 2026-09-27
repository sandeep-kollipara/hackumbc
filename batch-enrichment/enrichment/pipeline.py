from datetime import datetime, timezone
from itertools import islice
import hashlib
import json
import logging

from .crops import crop_objects
from .models import decode_image
from .repository import atomic_json, initial_object_id
from .schemas import ImageAnalysis


class Pipeline:
    def __init__(self, config, source, models, embedder, repository):
        self.config, self.source, self.models = config, source, models
        self.embedder, self.repository = embedder, repository

    def process(self, source_image, force=False):
        cfg = self.config
        token = hashlib.sha256(f"{source_image.revision}:{cfg.fingerprint()}".encode()).hexdigest()
        state_path = cfg.data_dir / "state" / cfg.collection / f"{source_image.source_id}.json"
        if not force and state_path.exists():
            state = json.loads(state_path.read_text())
            if state["token"] == token and self.repository.contains_source(source_image.source_id, state["ids"]):
                return "skipped"
        # A failed forced refresh must be retried, even if older vectors still exist.
        state_path.unlink(missing_ok=True)
        # Hashes, rather than S3 key paths, prevent traversal and duplicate-stem collisions.
        folder = cfg.data_dir / "artifacts" / source_image.source_id / token
        analysis_path, records_path = folder / "analysis.json", folder / "records.json"
        if force:
            # Do not let a failed forced analysis fall back to the previous cache.
            analysis_path.unlink(missing_ok=True)
            records_path.unlink(missing_ok=True)
        if not force and records_path.exists():
            records = json.loads(records_path.read_text())
        else:
            image = decode_image(self.source.download(source_image))
            if not force and analysis_path.exists():
                analysis = ImageAnalysis.model_validate_json(analysis_path.read_text())
            else:
                analysis = self.models.analyze(image)
                atomic_json(analysis_path, analysis.model_dump())
            records = []
            crops = iter(crop_objects(image, analysis, folder / "crops"))
            while batch := list(islice(crops, cfg.batch_size)):
                try:
                    images = self.embedder.embed_images([crop.image for crop in batch])
                    texts = self.models.embed_texts([crop.metadata["object_name"] for crop in batch])
                    if len(images) != len(batch) or len(texts) != len(batch):
                        raise RuntimeError("Embedding count does not match crops")
                    for crop, image_vector, text_vector in zip(batch, images, texts):
                        record_id = f"{source_image.source_id}:{token}:{crop.index}"
                        records.append({
                            **source_image.metadata(), **crop.metadata,
                            "record_id": record_id, "object_id": initial_object_id(record_id),
                            "processed_at": datetime.now(timezone.utc).isoformat(),
                            "vision_model": cfg.vision_model, "image_model": cfg.image_model,
                            "text_model": cfg.text_model,
                            "embedding": image_vector, "text_embedding": text_vector,
                        })
                finally:
                    for crop in batch:
                        crop.image.close()
            image.close()
            atomic_json(records_path, records)
        self.repository.replace_source(source_image.source_id, records)
        atomic_json(state_path, {"token": token, "ids": [r["record_id"] for r in records]})
        return "processed"

    def run(self, limit=None, force=False):
        counts = {"processed": 0, "skipped": 0, "failed": 0}
        images = self.source.images()
        if limit is not None:
            images = islice(images, limit)
        for image in images:
            try:
                result = self.process(image, force)
                counts[result] += 1
                logging.info("%s s3://%s/%s", result, image.bucket, image.key)
            except Exception:
                counts["failed"] += 1
                logging.exception("Failed s3://%s/%s; rerun to retry", image.bucket, image.key)
        return counts
