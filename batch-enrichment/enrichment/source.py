from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
import hashlib
import re

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SNAPSHOT = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6})_(.+)\.[^.]+$")


@dataclass(frozen=True)
class SourceImage:
    bucket: str
    key: str
    etag: str
    size: int
    modified: datetime

    @property
    def source_id(self):
        return hashlib.sha256(f"{self.bucket}\0{self.key}".encode()).hexdigest()

    @property
    def revision(self):
        return hashlib.sha256(f"{self.etag}\0{self.size}\0{self.modified.isoformat()}".encode()).hexdigest()

    def metadata(self):
        match = SNAPSHOT.match(PurePosixPath(self.key).name)
        timestamp, username = self.modified, ""
        if match:
            try:
                timestamp = datetime.strptime(match[1], "%Y-%m-%d_%H-%M-%S-%f").replace(tzinfo=timezone.utc)
                username = match[2]
            except ValueError:
                pass
        return {
            "source_id": self.source_id, "s3_bucket": self.bucket,
            "s3_key": self.key, "s3_etag": self.etag,
            "source_revision": self.revision,
            "parent_image": f"s3://{self.bucket}/{self.key}",
            "timestamp": timestamp.isoformat(), "username": username,
        }


class S3Source:
    def __init__(self, config, client=None):
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", region_name=config.region, config=Config(
                retries={"mode": "standard", "total_max_attempts": 4},
                connect_timeout=10, read_timeout=60,
            ))
        self.client, self.config = client, config

    def images(self):
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.config.bucket, Prefix=self.config.prefix,
        )
        for page in pages:
            for item in page.get("Contents", []):
                if PurePosixPath(item["Key"]).suffix.lower() in EXTENSIONS:
                    yield SourceImage(self.config.bucket, item["Key"], item["ETag"], item["Size"], item["LastModified"])

    def download(self, image):
        if image.size > self.config.max_image_bytes:
            raise ValueError("Image exceeds configured byte limit")
        response = self.client.get_object(Bucket=image.bucket, Key=image.key, IfMatch=image.etag)
        body = response["Body"]
        try:
            data = body.read(self.config.max_image_bytes + 1)
        finally:
            body.close()
        if len(data) > self.config.max_image_bytes:
            raise ValueError("Image exceeds configured byte limit")
        if len(data) != image.size or response.get("LastModified", image.modified) != image.modified:
            raise RuntimeError("S3 object changed during scan; retry the run")
        return data
