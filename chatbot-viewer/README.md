# Last Seen — Dash + Panel object viewer

Search recorded objects in the local Chroma database produced by `batch-enrichment`.
Dash provides the search form, user filter, and callbacks. Panel renders collapsible
result cards in a sandboxed iframe using standalone HTML with inline resources.
There is one web server, no separate Panel server, and no Streamlit dependency.

## Start from the top level

```bash
cd /home/sandeepk/Desktop/hackumbc
source .venv/bin/activate
python -m pip install -r chatbot-viewer/requirements.txt
./run-chatbot-viewer.sh
```

Open **http://127.0.0.1:8050**. Use `./run-chatbot-viewer.sh --port 8051` to choose
another port. The launcher uses the existing top-level `.venv` and works from any
working directory. Stop it with Ctrl+C.

The viewer reuses `batch-enrichment/.env`: the OpenAI key, embedding model,
dimensions, Chroma collection prefix, and data directory. You do not need to
copy credentials. Optional `chatbot-viewer/.env` values override enrichment
settings; shell variables take precedence over both files. Relative data paths
are resolved from `batch-enrichment` for consistency with its CLI. Restart the
viewer after changing environment settings. `OPENAI_QUERY_MODEL` defaults to
`gpt-6-luna` and controls question parsing only.

Run `./run-batch-enrichment.sh` first to populate Chroma and group observations
with `object_id`. This viewer never analyzes new S3 images or updates object
records. It reads crops from the local artifact directory; no AWS access is
needed by the viewer. Missing crops still show their textual context.

## Search

Examples:

- Where are my keys and headphones?
- Show the last seen cups.
- Where is my backpack?

Choose a username or search all users, and select how many distinct instances
to show **per requested object**. Each result shows the latest capture timestamp
in UTC, object crop, description, location, scene description, environment, and
lighting. Expand sighting details to inspect the source and identifiers.

The OpenAI parser extracts **all** named objects, up to eight per question.
Each is embedded with the same text model and dimensions used by ingestion.
The viewer scans stored text vectors in pages, computes cosine distances, and
identifies matching object groups. It then selects the newest observation of
each group by **capture timestamp**, even if an older observation was a closer
semantic match. Timestamps are parsed with their time zones; processing time is
never used as capture time. User filters apply both to matching and latest-row
selection. Groups remain distinct across usernames even when IDs coincide.

Results are sorted newest first, after semantic qualification. The default
maximum cosine distance is 0.35; lower is stricter. This is a starting value,
not a calibrated confidence score. If results are too broad, lower the tolerance;
if synonyms are missing, increase it. The match distance can refer to an older
label in the same group; the displayed metadata always comes from the latest
observation. The exact scan avoids losing recent matches behind a top-k cutoff,
but takes O(observations × requested objects × embedding dimensions) work and
keeps metadata per group in memory. It is intended for a local collection.

Ingestion embeds object **names**, not descriptions or scene context. Location,
color, and environment details are shown as context; they are not guaranteed
hard filters in natural-language questions. The username control is an exact
filter. Questions are independent rather than a conversational reasoning chain.

Answers are rendered directly from database records. There is no generated
location summary that could invent room details. Only the question/object names
are sent to OpenAI; the viewer does not send crops or database context. Each
search normally uses one parsing request and one embedding request, which incur
API usage. There is no GPU or DINOv2 work during viewer searches.

## Data freshness and limitations

- Run ingestion again when new images arrive, then use **Refresh library** and
  submit the query again. Existing displayed results remain a snapshot.
- A source image can produce multiple object sightings; the sidebar count is
  object records, not source images.
- Distinctness depends on enrichment's visual `object_id` clustering. If clustering
  has not run, separate observations can appear as separate instances. Similar
  objects can be incorrectly grouped; a sighting does not prove current location.
- The viewer takes the same file lock as enrichment for database reads. It reports
  a busy database during writes. Pending write journals must be repaired by
  rerunning enrichment; the viewer does not replay them.
- Missing databases are reported instead of silently creating empty collections.
  Embedding model/dimension mismatches are rejected.
- Invalid timestamps/vectors are reported. Local crop paths must resolve inside
  the configured artifact directory; metadata text is escaped in Panel HTML.
- The server binds only to `127.0.0.1`, with no login system. User filtering is a
  search convenience, not authorization. This is a local viewer, not a public app.

## Files

| File | Purpose |
|---|---|
| `chatbot_viewer.py` | Dash layout, callbacks, and local server |
| `result_view.py` | Panel cards and standalone HTML export |
| `RAG_delivery/object_search.py` | Query parsing, Chroma reads, latest-instance selection |
| `assets/style.css` | Responsive Dash styling |
| `tests/test_search.py` | Offline retrieval, database, UI, and rendering tests |
| `archive/` | Unmodified copies of the supplied Streamlit/Gemini/Firestore reference code |

```bash
.venv/bin/python -m pytest -q chatbot-viewer/tests
```

Tests use temporary Chroma databases and mocked OpenAI calls; they do not charge
an account. A live natural-language search requires your configured OpenAI key.
