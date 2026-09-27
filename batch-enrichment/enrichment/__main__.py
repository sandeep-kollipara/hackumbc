import argparse
from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path

from .config import Config


@contextmanager
def exclusive_lock(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".workflow.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another enrichment command is using this data directory") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def distance(value):
    result = float(value)
    if not 0 <= result <= 2:
        raise argparse.ArgumentTypeError("cosine distance must be between 0 and 2")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="S3 → GPT-6 Luna → DINOv2/OpenAI embeddings → local Chroma")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List matching S3 images without OpenAI calls")
    listing.add_argument("--limit", type=positive)
    run = commands.add_parser("run", help="Process images; completed source revisions are skipped")
    run.add_argument("--limit", type=positive, help="Maximum images scanned, including skipped images")
    run.add_argument("--force", action="store_true", help="Reanalyze scanned images, incurring new API calls")
    search = commands.add_parser("search", help="Query the image or text cosine index")
    query = search.add_mutually_exclusive_group(required=True)
    query.add_argument("--image", type=Path, help="Local image, preferably an object crop")
    query.add_argument("--text")
    search.add_argument("--limit", type=positive, default=30)
    search.add_argument("--threshold", type=distance, help="Maximum cosine distance; lower is stricter")
    search.add_argument("--username")
    grouping = commands.add_parser("cluster", help="Assign object IDs using union-find on image neighbors")
    grouping.add_argument("--threshold", type=distance, default=0.15)
    grouping.add_argument("--neighbors", type=positive, default=30)
    grouping.add_argument("--across-users", action="store_true")
    commands.add_parser("stats", help="Show local collection counts")
    get = commands.add_parser("get", help="Print full object document with both vectors")
    get.add_argument("record_id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = Config.from_env()
    if args.command in {"run", "list"} and not config.bucket:
        parser.error("Set AWS_S3_BUCKET_NAME in batch-enrichment/.env")
    if (args.command == "run" or (args.command == "search" and args.text is not None)) and not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY in batch-enrichment/.env or your environment")
    if args.command == "list":
        from itertools import islice
        from .source import S3Source
        images = S3Source(config).images()
        for image in islice(images, args.limit):
            print(json.dumps(image.metadata()))
        return 0
    from .repository import ChromaRepository
    with exclusive_lock(config.data_dir):
        repository = ChromaRepository(config)
        if args.command == "run":
            from .models import OpenAIModels, DinoEmbedder
            from .pipeline import Pipeline
            from .source import S3Source
            summary = Pipeline(config, S3Source(config), OpenAIModels(config), DinoEmbedder(config), repository).run(args.limit, args.force)
            print(json.dumps(summary, indent=2))
            return 1 if summary["failed"] else 0
        if args.command == "cluster":
            from .clustering import cluster
            output = cluster(repository, args.threshold, args.neighbors, args.across_users)
        elif args.command == "search":
            from .models import OpenAIModels, DinoEmbedder, decode_image
            if args.image:
                with decode_image(args.image.read_bytes()) as image:
                    vector = DinoEmbedder(config).embed_images([image])[0]
                kind = "image"
            else:
                if not args.text.strip():
                    parser.error("--text cannot be empty")
                vector, kind = OpenAIModels(config).embed_texts([args.text])[0], "text"
            output = repository.search(vector, kind, args.limit, args.threshold,
                                       {"username": args.username} if args.username is not None else None)
        elif args.command == "get":
            output = repository.get_object(args.record_id)
        else:
            output = {"image_records": repository.image.count(), "text_records": repository.text.count(),
                      "path": str(config.data_dir / "chroma")}
        print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
