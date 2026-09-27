from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    bucket: str = ""
    prefix: str = ""
    region: str = "us-east-1"
    data_dir: Path = ROOT / "data"
    collection: str = "rag_object_collection"
    vision_model: str = "gpt-6-luna"
    text_model: str = "text-embedding-3-small"
    text_dimensions: int = 768
    image_model: str = "facebook/dinov2-base"
    device: str = "auto"
    batch_size: int = 16
    max_image_bytes: int = 20 * 1024 * 1024

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        config = cls(
            bucket=os.getenv("AWS_S3_BUCKET_NAME", ""),
            prefix=os.getenv("S3_PREFIX", ""),
            region=os.getenv("AWS_REGION", "us-east-1"),
            data_dir=Path(os.getenv("ENRICHMENT_DATA_DIR", str(ROOT / "data"))).expanduser().resolve(),
            collection=os.getenv("CHROMA_COLLECTION", "rag_object_collection"),
            vision_model=os.getenv("OPENAI_VISION_MODEL", "gpt-6-luna"),
            text_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            text_dimensions=int(os.getenv("TEXT_EMBEDDING_DIMENSIONS", "768")),
            device=os.getenv("EMBEDDING_DEVICE", "auto"),
            batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "16")),
        )
        if config.batch_size < 1 or config.text_dimensions < 1:
            raise ValueError("Batch size and embedding dimensions must be positive")
        if config.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("EMBEDDING_DEVICE must be auto, cpu, or cuda")
        return config

    def fingerprint(self):
        # Bump pipeline_version when prompt/crop semantics change.
        return hashlib.sha256(json.dumps({
            "pipeline_version": 2, "vision": self.vision_model,
            "text": self.text_model, "dimensions": self.text_dimensions,
            "image": self.image_model,
            "collection": self.collection,
        }, sort_keys=True).encode()).hexdigest()
