"""Document lake：MinIO 原文 + Iceberg Evidence/Claims + Trino/OM 治理。"""

from __future__ import annotations

__all__ = ["ingest_document"]


def ingest_document(*args, **kwargs):
    from biomed_ontology.lake.ingest import ingest_document as _ingest

    return _ingest(*args, **kwargs)
