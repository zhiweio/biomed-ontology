"""数据源接入。open / licensed 双轨，全部经 registry 声明许可后才可写入术语层。"""

from biomed_ontology.ingest.seed import (
    AmbiguityRegistry,
    SeedBuildResult,
    build_from_seed,
    load_ambiguity_registry,
    load_seed_file,
)

__all__ = [
    "AmbiguityRegistry",
    "SeedBuildResult",
    "build_from_seed",
    "load_ambiguity_registry",
    "load_seed_file",
]
