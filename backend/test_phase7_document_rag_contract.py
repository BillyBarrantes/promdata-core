from copy import deepcopy

from app.services import document_rag


class _FakeResponse:
    def __init__(self, data):
        self.data = data
        self.error = None


class _FakeBucket:
    def __init__(self, storage: dict[str, dict[str, bytes]], bucket_name: str):
        self.storage = storage
        self.bucket_name = bucket_name

    def upload(self, path: str, content: bytes, _options=None):
        self.storage.setdefault(self.bucket_name, {})[path] = content

    def download(self, path: str) -> bytes:
        return self.storage.get(self.bucket_name, {}).get(path, b"")


class _FakeStorage:
    def __init__(self):
        self.buckets: dict[str, dict[str, bytes]] = {}

    def from_(self, bucket_name: str):
        return _FakeBucket(self.buckets, bucket_name)


class _FakeTable:
    def __init__(self, client, name: str):
        self.client = client
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self._limit: int | None = None
        self._order: tuple[str, bool] | None = None
        self._action: str | None = None
        self._payload = None

    def select(self, _fields: str):
        return self

    def eq(self, key: str, value):
        self.filters.append((key, value))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def order(self, key: str, desc: bool = False):
        self._order = (key, desc)
        return self

    def insert(self, payload):
        self._action = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = payload
        return self

    def delete(self):
        self._action = "delete"
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.name, [])

        def matches(row: dict) -> bool:
            return all(row.get(key) == value for key, value in self.filters)

        if self._action == "insert":
            payload = deepcopy(self._payload)
            if isinstance(payload, list):
                inserted = []
                for item in payload:
                    item = deepcopy(item)
                    item.setdefault("id", f"{self.name}-{len(rows) + len(inserted) + 1}")
                    inserted.append(item)
                rows.extend(inserted)
                return _FakeResponse(inserted)

            payload.setdefault("id", f"{self.name}-{len(rows) + 1}")
            rows.append(payload)
            return _FakeResponse([deepcopy(payload)])

        if self._action == "update":
            updated = []
            for row in rows:
                if matches(row):
                    row.update(deepcopy(self._payload))
                    updated.append(deepcopy(row))
            return _FakeResponse(updated)

        if self._action == "delete":
            remaining = []
            deleted = []
            for row in rows:
                if matches(row):
                    deleted.append(deepcopy(row))
                else:
                    remaining.append(row)
            self.client.tables[self.name] = remaining
            return _FakeResponse(deleted)

        result = [deepcopy(row) for row in rows if matches(row)]
        if self._order:
            key, desc = self._order
            result.sort(key=lambda item: item.get(key) or "", reverse=desc)
        if self._limit is not None:
            result = result[: self._limit]
        return _FakeResponse(result)


class _FakeServiceClient:
    def __init__(self):
        self.tables = {
            "team_members": [
                {"user_id": "00000000-0000-4000-8000-000000000001", "team_id": "00000000-0000-4000-8000-000000000002"},
            ],
            "knowledge_documents": [],
            "knowledge_document_chunks": [],
        }
        self.storage = _FakeStorage()

    def table(self, name: str):
        return _FakeTable(self, name)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    original_embedding = document_rag.generate_embedding_vector

    def fake_embedding(text: str) -> list[float]:
        normalized = text.lower()
        return document_rag._coerce_embedding_dimensions(
            [
                float(len(normalized)),
                float(normalized.count("ventas")),
                float(normalized.count("contingencia")),
                float(normalized.count("q4")),
            ],
            expected_dimensions=768,
        )

    document_rag.generate_embedding_vector = fake_embedding
    client = _FakeServiceClient()

    try:
        team_id = document_rag.resolve_user_team_id(user_id="00000000-0000-4000-8000-000000000001", service_client=client)
        _assert(team_id == "00000000-0000-4000-8000-000000000002", "Debe resolver team_id")

        storage_path = "user-1/okr_contingencia_q4.txt"
        raw_text = (
            "Plan de contingencia corporativo Q4. "
            "Si las ventas caen en Q4, priorizar promociones tácticas, "
            "refuerzo comercial y seguimiento semanal por canal."
        ).encode("utf-8")
        client.storage.from_("knowledge-documents").upload(storage_path, raw_text, {})

        document = document_rag.create_knowledge_document_record(
            user_id="00000000-0000-4000-8000-000000000001",
            team_id="00000000-0000-4000-8000-000000000002",
            title="Plan de Contingencia Q4",
            file_name="plan_q4.txt",
            mime_type="text/plain",
            storage_path=storage_path,
            file_size_bytes=len(raw_text),
            service_client=client,
        )
        _assert(document["status"] == "queued", "El documento debe iniciar en cola")

        indexed = document_rag.process_knowledge_document(
            document_id=document["id"],
            user_id="00000000-0000-4000-8000-000000000001",
            service_client=client,
        )
        _assert(indexed["status"] == "indexed", "El documento debe quedar indexado")
        _assert(indexed["chunk_count"] >= 1, "Debe generar al menos un chunk")
        _assert(
            len(client.tables["knowledge_document_chunks"][0]["embedding"]) == 768,
            "Cada chunk debe persistir embeddings de 768 dimensiones",
        )

        listed = document_rag.list_knowledge_documents(
            user_id="00000000-0000-4000-8000-000000000001",
            team_id="00000000-0000-4000-8000-000000000002",
            service_client=client,
        )
        _assert(len(listed) == 1, "Debe listar un documento")

        snippets = document_rag.search_knowledge_documents(
            user_id="00000000-0000-4000-8000-000000000001",
            team_id="00000000-0000-4000-8000-000000000002",
            query="Dada la caída de ventas en Q4, ¿qué dice el plan de contingencia?",
            service_client=client,
            limit=3,
        )
        _assert(len(snippets) >= 1, "Debe recuperar al menos un snippet relevante")
        context_block = document_rag.build_knowledge_context_block(snippets)
        _assert("Plan de Contingencia Q4" in context_block, "El bloque de contexto debe citar la fuente")
        _assert("ventas" in context_block.lower(), "El bloque debe contener el fragmento relevante")
    finally:
        document_rag.generate_embedding_vector = original_embedding

    print("OK: phase7 document rag contract")


if __name__ == "__main__":
    run()
