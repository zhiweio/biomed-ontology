"""原文对象存储（MinIO）。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from biomed_ontology.config import Settings, settings

__all__ = ["DocumentObjectStore", "ensure_buckets"]


@dataclass
class DocumentObjectStore:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool = False
    documents_bucket: str = "hmd-documents"
    lake_bucket: str = "hmd-lake"

    @classmethod
    def from_settings(cls, cfg: Settings | None = None) -> DocumentObjectStore:
        cfg = cfg or settings
        return cls(
            endpoint=cfg.minio_endpoint,
            access_key=cfg.minio_access_key,
            secret_key=cfg.minio_secret_key.get_secret_value(),
            secure=cfg.minio_secure,
            documents_bucket=cfg.minio_documents_bucket,
            lake_bucket=cfg.minio_lake_bucket,
        )

    def _client(self) -> Any:
        from minio import Minio

        host = self.endpoint.replace("http://", "").replace("https://", "")
        return Minio(
            host,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    def ensure_buckets(self) -> list[str]:
        client = self._client()
        created: list[str] = []
        for name in (self.documents_bucket, self.lake_bucket):
            if not client.bucket_exists(name):
                client.make_bucket(name)
                created.append(name)
        return created

    def put_document(
        self,
        *,
        source_id: str,
        doc_id: str,
        path: Path,
        content_type: str = "application/pdf",
    ) -> dict[str, str]:
        client = self._client()
        self.ensure_buckets()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        key = f"{source_id}/{doc_id.replace(':', '_')}/{path.name}"
        from io import BytesIO

        client.put_object(
            self.documents_bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        uri = f"s3://{self.documents_bucket}/{key}"
        return {"object_uri": uri, "object_key": key, "checksum_sha256": digest}


def ensure_buckets(cfg: Settings | None = None) -> list[str]:
    return DocumentObjectStore.from_settings(cfg).ensure_buckets()
