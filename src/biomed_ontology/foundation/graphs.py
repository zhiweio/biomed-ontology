"""GraphDB Named Graph 约定与 PROV 图 URI。

PoC 可用进程内 store；生产指向 GraphDB（Free 仅 Dev，生产用 SE/EE）。
"""

from __future__ import annotations

__all__ = [
    "BIOS_NS",
    "GRAPH_BIOMEDICAL",
    "GRAPH_INFERENCE",
    "GRAPH_KNOWLEDGE",
    "GRAPH_ONTOLOGY",
    "GRAPH_PROVENANCE",
    "HMD_NS",
    "NAMED_GRAPHS",
]

HMD_NS = "https://w3id.org/asliva/biomed-ontology/"
BIOS_NS = "https://bios.idea.edu.cn/concept/"

GRAPH_BIOMEDICAL = f"{HMD_NS}graph/biomedical"  # BIOS_v3
GRAPH_ONTOLOGY = f"{HMD_NS}graph/ontology"  # Enterprise Ontology TBox + mappings
GRAPH_KNOWLEDGE = f"{HMD_NS}graph/knowledge"  # Enterprise claims / relationships
GRAPH_PROVENANCE = f"{HMD_NS}graph/provenance"  # W3C PROV
GRAPH_INFERENCE = f"{HMD_NS}graph/inference"  # derived

NAMED_GRAPHS = (
    GRAPH_BIOMEDICAL,
    GRAPH_ONTOLOGY,
    GRAPH_KNOWLEDGE,
    GRAPH_PROVENANCE,
    GRAPH_INFERENCE,
)
