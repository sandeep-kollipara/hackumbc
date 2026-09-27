# S3 image batch enrichment

The supported workflow is `python -m enrichment`. It replaces the old batch steps
with S3 ingestion, GPT-6 Luna image analysis, object cropping, local DINOv2 image
embeddings, OpenAI text embeddings, two persistent Chroma indexes, similarity
search, and union-find object grouping. Everything new lives in this folder.
`streaming-server` requires no changes.

The original `vector_similarity/`, `vision/`, and `live_gaze_trigger.py` are retained
as **legacy reference code**. They still contain Google/Aria imports and are not
entry points for this workflow. No Google SDK or Aria device is needed here.

## Linux Mint setup

Once dependencies and `.env` are configured, run the complete workflow from the
repository root with the existing top-level virtual environment:

```bash
./run-batch-enrichment.sh
```

The script uses `.venv/bin/python` from the repository root without requiring
manual activation. It scans the configured S3 bucket/prefix once, skips completed
unchanged images, processes new or changed images, runs object clustering, and
prints Chroma counts. Run it again for subsequent uploads; it does not run on a
schedule or watch continuously. If ingestion fails, it exits with a nonzero status
before clustering; successful image checkpoints are retained for the next run.
The script can also be invoked by its absolute path from another directory.

Use Python 3.10–3.12 (tested with 3.12). Run from this directory:

```bash
cd /home/sandeepk/Desktop/hackumbc/batch-enrichment
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

For GPU acceleration, install the PyTorch/torchvision wheels appropriate to your
GPU and driver using the official [PyTorch installation selector](https://docs.pytorch.org/get-started/locally/),
then install this project's requirements. NVIDIA uses CUDA; supported AMD GPUs
use ROCm. A GPU alone does not guarantee a compatible PyTorch build. CPU mode
also works. The requirements accept PyTorch 2.5+ and Transformers 4.x.

```bash
python -m pip install -r requirements.txt
python -c 'import torch; print("GPU available:", torch.cuda.is_available())'
cp .env.example .env
```

Set `AWS_S3_BUCKET_NAME` and `AWS_REGION` to the same values used by the browser
app, and set `OPENAI_API_KEY`. This workflow loads **only its own** `.env`;
environment variables take precedence. Never commit credentials.

Boto3 uses the normal AWS credential chain: environment variables, `AWS_PROFILE`
and shared credentials, or an IAM role. The identity needs `s3:ListBucket` for the
bucket and `s3:GetObject` for the selected prefix. If objects use SSE-KMS, it also
needs the applicable `kms:Decrypt` permission. No S3 writes or deletions are made.

`S3_PREFIX` restricts a run to matching keys. The browser currently writes images
at the bucket root, so an empty prefix is appropriate for that app. `list` is a
read-only preview; it makes no OpenAI calls or model downloads:

```bash
python -m enrichment list --limit 10
python -m enrichment run --limit 5
python -m enrichment stats
python -m enrichment run
```

`--limit` limits images **scanned**, including skipped ones. Omit it to process
the whole prefix. Each run scans the current S3 listing once; it does not watch
for future uploads. Run the command again when more images arrive.

## Processing and recovery

1. Paginate S3 image keys (`jpg`, `jpeg`, `png`, `webp`, `bmp`) and download each
   selected object privately through Boto3. Use its ETag as a conditional read.
2. Apply EXIF orientation, convert to RGB, and send a JPEG to GPT-6 Luna through
   the Responses API. Structured output validates the scene/object schema and
   bounding boxes. Analysis describes visible objects without gaze-specific fields.
3. Convert 0–1000 normalized boxes using the actual oriented image dimensions.
   Save one crop per box; duplicate object names never overwrite one another.
4. Batch crops through `facebook/dinov2-base` locally: normalized 768-dimensional
   CLS embeddings, matching the original visual embedding method. GPU selection
   is automatic; `EMBEDDING_DEVICE=cpu` or `cuda` overrides it. Lower
   `EMBEDDING_BATCH_SIZE` if GPU memory is limited. Initial use downloads model
   weights from Hugging Face. GPT analysis and text embedding run remotely;
   only DINOv2 benefits from your local GPU.
5. Embed **object names only**, matching the latest legacy batch script, using
   `text-embedding-3-small` with 768 dimensions. GPT-6 Luna performs analysis;
   it is not used as an embedding model.
6. Upsert both Chroma indexes and record successful completion locally.

This is a local batch-processing workflow issuing synchronous OpenAI requests,
not the asynchronous OpenAI Batch API. It processes one source image at a time,
with configurable crop embedding batches. OpenAI transient failures have three
SDK retries, S3 has four total attempts; validation failures and refusals are
reported without infinite retries. The run continues with other images and
exits with code 1 if any image failed.

Source identity is bucket + full key. Checkpoints include ETag, size, modification
time, model configuration, and pipeline version. Re-running skips completed,
unchanged sources only when both indexes contain the expected IDs. Analysis and
prepared vectors are cached, allowing recovery without repeating completed API
work. `--force` reanalyzes scanned images and can incur new charges. Changing the
vision configuration reprocesses sources; changing the embedding model or
dimensions requires a new `CHROMA_COLLECTION` to prevent mixing vector spaces.

Per-source write journals repair interrupted writes to the two collections on
the next command. A Linux file lock serializes commands against one data
directory; use the CLI/repository rather than opening the Chroma files from
another writer. This is process-crash recovery, not a cross-collection database
transaction or a power-loss durability guarantee. If an index write fails,
rerun the command before consuming that source's records externally.

Changed objects replace their old records when processed successfully. Deleted
S3 keys are **not** automatically removed locally. Old artifact revisions remain
for inspection and may consume disk space. Empty scenes are checkpointed too.
Local crops, metadata, and vector caches contain image-derived data; keep the
data directory on an appropriately protected local disk.

## Schema and indexes

Chroma supports one embedding per record in a collection, so the equivalent of
the two Firestore vector fields is two collections joined by `record_id`:

| Collection | Logical field | Model | Dimensions | Index |
|---|---|---|---|---|
| `rag_object_collection_image` | `embedding` | DINOv2 base | 768 | HNSW cosine |
| `rag_object_collection_text` | `text_embedding` | OpenAI text-embedding-3-small | 768 | HNSW cosine |

The collection prefix is configurable. Chroma builds these indexes automatically;
there is no Firestore index deployment. HNSW is approximate, so this preserves
the vector fields and cosine metric, not Firestore's physical index type or exact
neighbor ordering. OpenAI text vectors are a new embedding space; existing Gemini
vectors cannot be reused or compared with them.

Each index stores identical JSON documents and scalar metadata for filtering.
JSON preserves `object_id`, `object_name`, `object_description`, `object_location`,
`bounding_boxes`, `crop_path`, `parent_image`,
`timestamp`, and nested `scene_meta`. `bounding_boxes` consistently follows the
live vision schema (a list of dictionaries); the legacy batch script instead
saved one dictionary. `crop_box` identifies the individual box for this crop.
`get` reconstructs the complete logical document with both vector fields.

`parent_image` is the source S3 URI (the old batch script accidentally used the
crop filename). Timestamps are UTC ISO-8601 strings rather than Firestore
timestamps. For browser filenames `YYYY-MM-DD_HH-MM-SS-ffffff_username.jpeg`,
the timestamp and username are parsed without losing underscores or hyphens.
Other filenames use S3 LastModified and an empty username. There is no synthetic
`run_id`: the frontend does not emit a session ID, and the S3 key and capture
timestamp already provide the relevant context. Additional fields preserve source identity,
model provenance, image size, crop pixels, and processing time.

The current schema removes the legacy `is_gaze_target` and synthetic `run_id`
fields. Its pipeline version invalidates older processing checkpoints. If you
already processed images with the earlier workflow, run `python -m enrichment run`
without a limit to reanalyze available sources and replace their old records, then
rerun `cluster`. This incurs new API calls; old artifact revisions and records for
sources no longer present in S3 are retained. No existing data is migrated merely
by updating the code.

## Search and object grouping

```bash
python -m enrichment search --text "coffee cup" --limit 10 --username alice
python -m enrichment search --image /absolute/path/to/object_crop.jpg --threshold 0.15
python -m enrichment cluster --threshold 0.15 --neighbors 30
python -m enrichment get 'record_id_from_a_search_result'
```

Text queries use the same text embedding model and dimensions as ingestion;
image queries use DINOv2. Search returns lower-is-better **cosine distance**
(`1 - cosine similarity`). A distance of 0.15 corresponds to similarity 0.85.
Search has no threshold unless supplied. Image and text distances should be
tuned independently against your own examples. Image queries work best with a
single object crop, since the stored vectors represent crops.

New records start with deterministic individual `object_id` values. Run `cluster`
after ingestion to group visually similar records by connected components of the
image neighbor graph. It joins transitive matches correctly (A–B and B–C form one
group even when A–C is farther apart). Grouping is per username by default;
`--across-users` explicitly allows shared groups. Unknown usernames share the
empty-username group. No object-name restriction is imposed, matching the legacy
visual similarity logic.

Clustering examines approximate nearest neighbors, not every pair. Increase
`--neighbors` for dense groups at greater cost. Similar appearance does not prove
physical identity; review and tune the 0.15 starting threshold. Component IDs are
deterministic for a fixed graph but may change when records are added, removed,
or groups merge. Re-run clustering after ingestion or an interrupted clustering
command. Both indexes receive the same IDs. Clustering streams vectors but keeps
an O(number of records) union-find map in memory; local Chroma indexes also use RAM.

## Files and validation

```text
enrichment/          New supported Python package and CLI
tests/               Offline tests using real temporary Chroma + mocked remote services
data/chroma/         Persistent local vector database
data/artifacts/      Analysis JSON, crops, and prepared vectors by source/config revision
data/state/          Completed source checkpoints
data/journal/        Pending two-index writes for recovery
```

Set `ENRICHMENT_DATA_DIR` to relocate all generated data. Defaults are resolved
relative to this project; an explicitly supplied relative path follows the current
working directory. The data directory, `.env`, and virtual environment are ignored
by Git. Back up the entire data directory while no command is running.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover real Chroma persistence and cosine searches, nested schema recovery,
interrupted writes, duplicate names, arbitrary dimensions, EXIF orientation,
S3 pagination and conditional reads, resumability, empty scenes, transitive
grouping, and actual OpenAI SDK serialization/parsing with mocked HTTP responses.
They do not require cloud credentials, charge an API account, or download DINOv2.
A live smoke run with your AWS/OpenAI credentials and GPU is still required.

API references: [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[OpenAI embeddings](https://developers.openai.com/api/docs/guides/embeddings),
[Chroma index configuration](https://docs.trychroma.com/docs/collections/configure).
