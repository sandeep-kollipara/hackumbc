"""Paired Chroma indexes preserve the two-vector logical document schema."""
import json
import uuid


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def scalar_metadata(record):
    # Chroma metadata cannot hold nested dictionaries. Full nested data is in document.
    return {key: value for key, value in record.items()
            if isinstance(value, (str, int, float, bool))}


class ChromaRepository:
    def __init__(self, config):
        import chromadb
        self.client = chromadb.PersistentClient(path=str(config.data_dir / "chroma"))
        self.journal = config.data_dir / "journal" / config.collection
        self.image = self._collection(config.collection + "_image", config.image_model, 768)
        self.text = self._collection(config.collection + "_text", config.text_model, config.text_dimensions)
        # A killed process may have written only one collection. Replay before reads.
        if self.journal.exists():
            for path in sorted(self.journal.glob("*.json")):
                payload = json.loads(path.read_text())
                self._replace(payload["source_id"], payload["records"])
                path.unlink()

    def _collection(self, name, model, dimensions):
        metadata = {"embedding_model": model, "dimensions": dimensions, "schema_version": 1}
        collection = self.client.get_or_create_collection(
            name, embedding_function=None, metadata=metadata,
            configuration={"hnsw": {"space": "cosine"}},
        )
        if collection.metadata != metadata or collection.configuration["hnsw"]["space"] != "cosine":
            raise ValueError(f"Incompatible index {name}; use a new CHROMA_COLLECTION for changed models/schema")
        return collection

    def contains_source(self, source_id, ids):
        return all(set(collection.get(where={"source_id": source_id}, include=[])["ids"]) == set(ids)
                   for collection in (self.image, self.text))

    def replace_source(self, source_id, records):
        path = self.journal / f"{source_id}.json"
        atomic_json(path, {"source_id": source_id, "records": records})
        self._replace(source_id, records)
        path.unlink()

    def _replace(self, source_id, records):
        for collection, field in ((self.image, "embedding"), (self.text, "text_embedding")):
            # Delete only obsolete IDs, not current ones; upsert repairs partial writes.
            wanted = {record["record_id"] for record in records}
            old = collection.get(where={"source_id": source_id}, include=[])["ids"]
            obsolete = [record_id for record_id in old if record_id not in wanted]
            if obsolete:
                collection.delete(ids=obsolete)
            for start in range(0, len(records), 128):
                batch = records[start:start + 128]
                documents = [{k: v for k, v in record.items() if k not in {"embedding", "text_embedding"}}
                             for record in batch]
                collection.upsert(
                    ids=[r["record_id"] for r in batch],
                    embeddings=[r[field] for r in batch],
                    documents=[json.dumps(doc, ensure_ascii=False) for doc in documents],
                    metadatas=[scalar_metadata(doc) for doc in documents],
                )

    def search(self, vector, kind="image", limit=30, threshold=None, where=None):
        collection = self.image if kind == "image" else self.text
        count = collection.count()
        if not count:
            return []
        args = {"where": where} if where else {}
        result = collection.query(query_embeddings=[vector], n_results=min(limit, count),
                                  include=["documents", "distances"], **args)
        return [{**json.loads(doc), "distance": float(distance)}
                for doc, distance in zip(result["documents"][0], result["distances"][0])
                if threshold is None or distance <= threshold]

    def get_object(self, record_id):
        """Reconstruct the original logical document, including both vector fields."""
        image = self.image.get(ids=[record_id], include=["documents", "embeddings"])
        text = self.text.get(ids=[record_id], include=["embeddings"])
        if not image["ids"] or not text["ids"]:
            raise KeyError(record_id)
        return {**json.loads(image["documents"][0]),
                "embedding": image["embeddings"][0].tolist(),
                "text_embedding": text["embeddings"][0].tolist()}

    def image_rows(self):
        for offset in range(0, self.image.count(), 256):
            batch = self.image.get(limit=256, offset=offset, include=["embeddings", "documents"])
            yield from zip(batch["ids"], batch["embeddings"], batch["documents"])

    def assign_ids(self, assignments):
        for collection in (self.image, self.text):
            ids = list(assignments)
            for offset in range(0, len(ids), 128):
                batch = collection.get(ids=ids[offset:offset + 128], include=["documents", "embeddings"])
                documents = [dict(json.loads(doc), object_id=assignments[record_id])
                             for record_id, doc in zip(batch["ids"], batch["documents"])]
                if documents:
                    collection.update(ids=batch["ids"],
                                      embeddings=batch["embeddings"],
                                      documents=[json.dumps(doc) for doc in documents],
                                      metadatas=[scalar_metadata(doc) for doc in documents])


def initial_object_id(record_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, record_id))
