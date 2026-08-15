"""Stage B/C：candidates → filter/rank → tool enrich → proposals（不写本体）。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from biomed_ontology.evolution import KgclOp
from biomed_ontology.foundation.ids import is_enterprise_id, normalize_alias_key
from biomed_ontology.foundation.paths import REPO_ROOT, ZINGG_MATCHES_PATH
from biomed_ontology.foundation.resolve import load_zingg_matches

__all__ = [
    "DEFAULT_POLICY_PATH",
    "PROPOSALS_DIR",
    "EvolveEnrichResult",
    "FilterPolicy",
    "filter_candidates",
    "load_candidates_files",
    "load_filter_policy",
    "load_gold_null_keys",
    "run_enrich",
]

DEFAULT_POLICY_PATH = REPO_ROOT / "ontology" / "policies" / "evolve_filter.yaml"
PROPOSALS_DIR = REPO_ROOT / "data" / "releases" / "foundation_proposals"
CANDIDATES_DIR = REPO_ROOT / "data" / "releases" / "foundation_candidates"


@dataclass
class FilterPolicy:
    min_chars: int = 2
    max_chars: int = 80
    deny_patterns: list[re.Pattern[str]] = field(default_factory=list)
    allow_patterns: list[re.Pattern[str]] = field(default_factory=list)
    min_overlap: float = 0.15
    accept_substring: bool = True
    drop_methods: set[str] = field(default_factory=set)
    min_confidence_by_method: dict[str, float] = field(default_factory=dict)
    min_occurrences: int = 1
    skip_confidence: float = 0.95
    gold_resolve_path: Path | None = None
    dismiss_expect_null: bool = True
    weight_occurrences: float = 1.0
    weight_confidence: float = 1.0
    weight_evidence: float = 0.5
    weight_overlap: float = 0.3
    deny_source_prefixes: list[str] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvolveEnrichResult:
    generated_at: str
    proposals_path: Path
    kgcl_path: Path
    proposals: list[dict[str, Any]] = field(default_factory=list)
    dismissed: list[dict[str, Any]] = field(default_factory=list)
    soft_downrank: list[dict[str, Any]] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    policy_path: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["proposals_path"] = str(self.proposals_path)
        data["kgcl_path"] = str(self.kgcl_path)
        return data


def load_filter_policy(path: Path | None = None) -> FilterPolicy:
    p = path or DEFAULT_POLICY_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    raw = raw or {}
    mention = raw.get("mention") or {}
    qm = raw.get("query_mention") or {}
    methods = raw.get("methods") or {}
    occ = raw.get("occurrences") or {}
    skipped = raw.get("skipped") or {}
    gold = raw.get("gold") or {}
    rank = raw.get("rank") or {}
    sources = raw.get("sources") or {}
    llm = dict(raw.get("llm") or {})

    def _compile(patterns: list[Any]) -> list[re.Pattern[str]]:
        out: list[re.Pattern[str]] = []
        for pat in patterns or []:
            out.append(re.compile(str(pat)))
        return out

    gold_path = gold.get("resolve_path")
    return FilterPolicy(
        min_chars=int(mention.get("min_chars", 2)),
        max_chars=int(mention.get("max_chars", 80)),
        deny_patterns=_compile(list(mention.get("deny_patterns") or [])),
        allow_patterns=_compile(list(mention.get("allow_patterns") or [])),
        min_overlap=float(qm.get("min_overlap", 0.35)),
        accept_substring=bool(qm.get("accept_substring", True)),
        drop_methods={str(x) for x in (methods.get("drop_methods") or [])},
        min_confidence_by_method={
            str(k): float(v) for k, v in (methods.get("min_confidence_by_method") or {}).items()
        },
        min_occurrences=int(occ.get("min", 1)),
        skip_confidence=float(skipped.get("min_confidence", 0.95)),
        gold_resolve_path=(REPO_ROOT / str(gold_path)) if gold_path else None,
        dismiss_expect_null=bool(gold.get("dismiss_expect_null", True)),
        weight_occurrences=float(rank.get("weight_occurrences", 1.0)),
        weight_confidence=float(rank.get("weight_confidence", 1.0)),
        weight_evidence=float(rank.get("weight_evidence", 0.5)),
        weight_overlap=float(rank.get("weight_overlap", 0.3)),
        deny_source_prefixes=[str(x) for x in (sources.get("deny_source_prefixes") or [])],
        llm=llm,
        raw=raw,
    )


def load_gold_null_keys(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = raw.get("cases") or raw.get("items") or raw
    if not isinstance(cases, list):
        return set()
    keys: set[str] = set()
    for row in cases:
        if not isinstance(row, dict):
            continue
        if row.get("expect") is None and "text" in row:
            keys.add(normalize_alias_key(str(row["text"])))
    return keys


def load_candidates_files(
    paths: list[Path] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Load one or more ``*.candidates.json``; merge candidates with source stamp."""
    files: list[Path] = list(paths) if paths else sorted(CANDIDATES_DIR.glob("*.candidates.json"))
    if not files:
        raise FileNotFoundError(f"no candidates.json under {CANDIDATES_DIR}")

    merged: list[dict[str, Any]] = []
    skipped_keys: set[str] = set()
    for fp in files:
        payload = json.loads(fp.read_text(encoding="utf-8"))
        stamp = str(payload.get("generated_at") or fp.stem)
        for sk in payload.get("skipped") or []:
            m = sk.get("mention") or sk.get("query")
            if m:
                skipped_keys.add(normalize_alias_key(str(m)))
        for cand in payload.get("candidates") or []:
            row = dict(cand)
            row["_source_file"] = str(fp)
            row["_generated_at"] = stamp
            row["mention_key"] = normalize_alias_key(str(row.get("mention") or ""))
            row.setdefault("query", row.get("mention"))
            merged.append(row)
    # mark already-skipped from same batch
    for row in merged:
        if row["mention_key"] in skipped_keys:
            row["_batch_skipped"] = True
    return merged, [str(f) for f in files]


def _token_set(text: str) -> set[str]:
    key = normalize_alias_key(text)
    parts = re.split(r"[^\w\u4e00-\u9fff]+", key)
    return {p for p in parts if p}


def query_mention_overlap(query: str, mention: str, *, accept_substring: bool) -> float:
    q = normalize_alias_key(query or "")
    m = normalize_alias_key(mention or "")
    if not q or not m:
        return 0.0
    if q == m:
        return 1.0
    # 短碎片（如 BERN2 切出的 xyz）不得靠子串对齐抬分
    if accept_substring and len(m) >= 4 and (m in q or q in m):
        return 1.0
    qt, mt = _token_set(q), _token_set(m)
    if not qt or not mt:
        return 0.0
    return len(qt & mt) / len(qt | mt)


def _enterprise_targets(cand: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for xid in cand.get("external_ids") or []:
        if is_enterprise_id(str(xid)):
            out.append(str(xid))
    canon = cand.get("canonical_entity")
    if canon and is_enterprise_id(str(canon)):
        out.insert(0, str(canon))
    return list(dict.fromkeys(out))


def filter_candidates(
    candidates: list[dict[str, Any]],
    policy: FilterPolicy,
    *,
    occurrence_counts: dict[str, int] | None = None,
    gold_null_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (keep, dismissed_L0, soft_downrank)."""
    counts = occurrence_counts or Counter(
        str(c.get("mention_key") or normalize_alias_key(str(c.get("mention") or "")))
        for c in candidates
    )
    if gold_null_keys is not None:
        gold_nulls = gold_null_keys
    else:
        gold_nulls = load_gold_null_keys(policy.gold_resolve_path)

    keep: list[dict[str, Any]] = []
    dismissed: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    seen: set[str] = set()

    for cand in candidates:
        mention = str(cand.get("mention") or "").strip()
        query = str(cand.get("query") or mention)
        key = str(cand.get("mention_key") or normalize_alias_key(mention))
        occ = int(counts.get(key, 1))
        overlap = query_mention_overlap(query, mention, accept_substring=policy.accept_substring)
        method = str(cand.get("resolution_method") or "")
        conf = float(cand.get("confidence") or 0.0)
        reasons: list[str] = []

        if cand.get("_batch_skipped"):
            reasons.append("batch_skipped")
        if not mention or len(mention) < policy.min_chars:
            reasons.append("too_short")
        if len(mention) > policy.max_chars:
            reasons.append("too_long")
        if policy.allow_patterns and not any(p.search(mention) for p in policy.allow_patterns):
            reasons.append("allow_miss")
        for pat in policy.deny_patterns:
            if pat.search(mention):
                reasons.append(f"deny_pattern:{pat.pattern}")
                break
        if key in gold_nulls and policy.dismiss_expect_null:
            reasons.append("gold_expect_null")
        if method in policy.drop_methods:
            reasons.append(f"drop_method:{method}")
        min_conf = policy.min_confidence_by_method.get(method)
        if min_conf is not None and conf < min_conf:
            reasons.append("below_method_confidence")
        if overlap < policy.min_overlap:
            reasons.append("low_query_overlap")
        if occ < policy.min_occurrences:
            reasons.append("below_min_occurrences")

        row = {
            **cand,
            "mention_key": key,
            "occurrences": occ,
            "query_overlap": round(overlap, 4),
            "filter_reasons": reasons,
        }

        if reasons:
            # hard → L0 dismiss（LLM 不可 rescue）；soft/borderline → 可进 LLM
            hard = {
                "too_short",
                "too_long",
                "allow_miss",
                "gold_expect_null",
                "batch_skipped",
            }
            hard |= {
                r for r in reasons if r.startswith("deny_pattern:") or r.startswith("drop_method:")
            }
            if any(r in hard or r.startswith("deny_") or r.startswith("drop_") for r in reasons):
                row["risk_tier"] = "L0"
                dismissed.append(row)
                continue
            soft_reasons = {
                "low_query_overlap",
                "below_min_occurrences",
                "below_method_confidence",
            }
            if any(r in soft_reasons for r in reasons):
                row["risk_tier"] = "L0"
                row["borderline"] = True
                soft.append(row)
                dismissed.append(row)
                continue

        dedupe_key = key
        if dedupe_key in seen:
            row["filter_reasons"] = ["dedupe"]
            row["risk_tier"] = "L0"
            dismissed.append(row)
            continue
        seen.add(dedupe_key)

        score = (
            policy.weight_occurrences * float(occ)
            + policy.weight_confidence * conf
            + policy.weight_overlap * overlap
        )
        row["rank_score"] = round(score, 4)
        keep.append(row)

    keep.sort(key=lambda r: (-float(r.get("rank_score") or 0), r.get("mention_key") or ""))
    return keep, dismissed, soft


def _proposal_id(mention_key: str, op: str, target: str | None) -> str:
    raw = f"{op}|{mention_key}|{target or ''}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"HMDPROP:{digest}"


def _gather_evidence(
    cand: dict[str, Any],
    *,
    api: Any | None,
    zingg: dict[str, tuple[str, float]],
    skip_tools: bool,
) -> dict[str, Any]:
    mention = str(cand.get("mention") or "")
    key = normalize_alias_key(mention)
    evidence: dict[str, Any] = {
        "resolver": None,
        "bios": [],
        "bern2": {
            "external_ids": list(cand.get("external_ids") or []),
            "confidence": cand.get("confidence"),
            "method": cand.get("resolution_method"),
        },
        "zingg": None,
        "llm_rationale": None,
    }
    if key in zingg:
        eid, score = zingg[key]
        evidence["zingg"] = {"enterprise_id": eid, "score": score}
    if skip_tools or api is None:
        return evidence
    try:
        resolved = api.resolve_entity(mention)
        hits = list(resolved.get("resolved") or [])
        if hits:
            h0 = hits[0]
            evidence["resolver"] = {
                "method": h0.get("resolution_method"),
                "confidence": h0.get("confidence"),
                "canonical_entity": h0.get("canonical_entity"),
                "external_ids": list(h0.get("external_ids") or []),
            }
    except Exception as exc:
        evidence["resolver"] = {"error": str(exc)}
    try:
        bios = api.lookup_bios_concept(query=mention, max_surfaces=4, max_neighbors=4)
        cards = list(bios.get("concepts") or bios.get("hits") or [])[:3]
        evidence["bios"] = cards
    except Exception as exc:
        evidence["bios"] = [{"error": str(exc)}]
    return evidence


def _build_proposal(cand: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    mention = str(cand["mention"])
    key = str(cand["mention_key"])
    targets = _enterprise_targets(cand)
    zingg = evidence.get("zingg") or {}
    resolver = evidence.get("resolver") or {}
    if resolver.get("canonical_entity") and is_enterprise_id(str(resolver["canonical_entity"])):
        targets.insert(0, str(resolver["canonical_entity"]))
    if zingg.get("enterprise_id"):
        targets.append(str(zingg["enterprise_id"]))
    targets = list(dict.fromkeys(t for t in targets if is_enterprise_id(t)))

    conf = float(cand.get("confidence") or 0.0)
    if zingg.get("score"):
        conf = max(conf, float(zingg["score"]))

    cand_ent_ids = [str(x) for x in (cand.get("external_ids") or []) if is_enterprise_id(str(x))]
    if targets:
        target = targets[0]
        if cand_ent_ids or (
            resolver.get("canonical_entity") and is_enterprise_id(str(resolver["canonical_entity"]))
        ):
            op = "create_synonym"
            write_surface = "dictionary"
            kgcl = KgclOp(
                "create synonym",
                target,
                new_value=mention,
                qualifier="exact",
                signal_id=_proposal_id(key, op, target),
                rationale="tool-enriched L1 synonym proposal",
            ).to_kgcl()
        elif evidence.get("zingg"):
            op = "fuzzy_link"
            write_surface = "zingg_matches"
            kgcl = f"# fuzzy link '{mention}' -> {target} (zingg score={zingg.get('score')})"
        else:
            op = "create_synonym"
            write_surface = "dictionary"
            kgcl = KgclOp(
                "create synonym",
                target,
                new_value=mention,
                qualifier="exact",
                signal_id=_proposal_id(key, op, target),
                rationale="tool-enriched L1 synonym proposal",
            ).to_kgcl()
        risk = "L1"
    else:
        target = None
        op = "create_node"
        write_surface = "entities_draft"
        risk = "L3"
        kgcl = KgclOp(
            "create node",
            f"NEW:{key}",
            new_value=mention,
            signal_id=_proposal_id(key, op, None),
            rationale="no enterprise target; draft only",
        ).to_kgcl()
    evid_n = sum(
        1
        for k in ("resolver", "bios", "zingg", "bern2")
        if evidence.get(k)
        and not (isinstance(evidence.get(k), dict) and evidence[k].get("error"))
        and evidence.get(k) != []
    )
    rank = float(cand.get("rank_score") or 0) + 0.5 * evid_n

    return {
        "proposal_id": _proposal_id(key, op, target),
        "mention": mention,
        "mention_key": key,
        "query": cand.get("query"),
        "occurrences": cand.get("occurrences", 1),
        "sources": ["evolve-mine", cand.get("_source_file")],
        "op": op,
        "target_enterprise_id": target,
        "write_surface": write_surface,
        "evidence": evidence,
        "risk_tier": risk,
        "confidence": round(conf, 4),
        "rank_score": round(rank, 4),
        "status": "pending_approval",
        "kgcl": kgcl,
        "query_overlap": cand.get("query_overlap"),
        "resolution_method": cand.get("resolution_method"),
    }


def run_enrich(
    *,
    from_paths: list[Path] | None = None,
    policy_path: Path | None = None,
    out_dir: Path | None = None,
    skip_tools: bool = False,
    use_llm: bool = True,
    chat: Any | None = None,
    world: Any | None = None,
    progress: Any | None = None,
    llm_progress: Any | None = None,
) -> EvolveEnrichResult:
    """Filter (+ LLM adjudicate by default) + enrich → proposals.jsonl + KGCL."""
    from biomed_ontology.foundation.evolve_llm_filter import adjudicate_candidates

    policy = load_filter_policy(policy_path)
    candidates, source_files = load_candidates_files(from_paths)
    gold_nulls = (
        load_gold_null_keys(policy.gold_resolve_path) if policy.dismiss_expect_null else set()
    )
    keep, dismissed, soft = filter_candidates(candidates, policy, gold_null_keys=gold_nulls)

    llm_cfg = dict(policy.llm or {})
    llm_cfg["enabled"] = bool(use_llm) and bool(llm_cfg.get("enabled", True))
    if not use_llm:
        llm_cfg["enabled"] = False
    prompt_rel = llm_cfg.get("prompt_path")
    prompt_path = (REPO_ROOT / str(prompt_rel)) if prompt_rel else None

    keep, dismissed, soft, llm_stats = adjudicate_candidates(
        keep,
        dismissed,
        soft,
        llm_policy=llm_cfg,
        chat=chat,
        prompt_path=prompt_path,
        progress=llm_progress,
    )

    api = None
    if not skip_tools:
        from biomed_ontology.foundation.api import FoundationApi
        from biomed_ontology.foundation.world import load_world_model

        wm = world or load_world_model()
        api = FoundationApi(wm)

    zingg = load_zingg_matches(ZINGG_MATCHES_PATH)
    proposals: list[dict[str, Any]] = []
    bar = progress
    if bar is not None:
        bar.total = len(keep)

    for cand in keep:
        # Drop already high-confidence mapped on re-resolve
        if api is not None:
            try:
                resolved = api.resolve_entity(str(cand["mention"]))
                hits = list(resolved.get("resolved") or [])
                if hits:
                    h0 = hits[0]
                    conf = float(h0.get("confidence") or 0)
                    if h0.get("canonical_entity") and conf >= policy.skip_confidence:
                        dismissed.append(
                            {
                                **cand,
                                "risk_tier": "L0",
                                "filter_reasons": [f"already_mapped>={policy.skip_confidence}"],
                            }
                        )
                        if bar is not None:
                            bar.update(1)
                        continue
            except Exception:
                pass
        evidence = _gather_evidence(cand, api=api, zingg=zingg, skip_tools=skip_tools)
        if cand.get("llm_rationale"):
            evidence["llm_rationale"] = cand.get("llm_rationale")
            evidence["llm_labels"] = cand.get("llm_labels")
        prop = _build_proposal(cand, evidence)
        # L3 create_node stays pending but not batch-auto; still emit
        proposals.append(prop)
        if bar is not None:
            bar.update(1)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = out_dir or PROPOSALS_DIR
    dest.mkdir(parents=True, exist_ok=True)
    proposals_path = dest / f"{stamp}.proposals.jsonl"
    kgcl_path = dest / f"{stamp}.kgcl"

    with proposals_path.open("w", encoding="utf-8") as fh:
        for prop in proposals:
            fh.write(json.dumps(prop, ensure_ascii=False) + "\n")

    kgcl_lines = [
        f"# Foundation evolve-enrich {stamp}",
        "# Executable KGCL for approved L1 ops; L3 create node remains draft",
        f"# sources: {', '.join(Path(s).name for s in source_files)}",
        "",
    ]
    for prop in proposals:
        if prop.get("risk_tier") == "L3":
            kgcl_lines.append(f"# L3 draft: {prop.get('kgcl')}")
        else:
            kgcl_lines.append(str(prop.get("kgcl") or ""))
        kgcl_lines.append("")
    kgcl_path.write_text("\n".join(kgcl_lines).rstrip() + "\n", encoding="utf-8")

    tier_counts: Counter[str] = Counter(str(p.get("risk_tier")) for p in proposals)
    counts: dict[str, Any] = {
        "input": len(candidates),
        "keep": len(keep),
        "dismissed": len(dismissed),
        "soft_downrank": len(soft),
        "proposals": len(proposals),
        "L1": int(tier_counts.get("L1", 0)),
        "L2": int(tier_counts.get("L2", 0)),
        "L3": int(tier_counts.get("L3", 0)),
    }
    counts.update(llm_stats.as_dict())
    return EvolveEnrichResult(
        generated_at=stamp,
        proposals_path=proposals_path,
        kgcl_path=kgcl_path,
        proposals=proposals,
        dismissed=dismissed,
        soft_downrank=soft,
        source_files=source_files,
        policy_path=str(policy_path or DEFAULT_POLICY_PATH),
        counts=counts,
    )
