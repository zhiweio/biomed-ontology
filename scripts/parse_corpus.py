"""把 `fetch_corpus.py` 下回来的 PDF 批量解析成语料 YAML。

与 `hmd parse` 的关系：那条命令处理单篇，这里只是按 `data/raw/corpus.json`
把它跑一遍，顺带把标题、许可、来源 ID 填对 —— 手敲九次 `hmd parse` 迟早会敲错一个。

法务闸门：PyMuPDF 是 AGPL-3.0/商业双授权，`NOTICE` 里仍标 review=pending。
本脚本**不会**替你绕过闸门，你必须自己设 `HMD_ACCEPT_UNCLEARED_COMPONENTS=true`，
这样"谁在什么时候接受了这个风险"至少留在命令历史里。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from biomed_ontology._generated.hmd_concept import LanguageEnum, LicenseTierEnum
from biomed_ontology._generated.hmd_fact import DocTypeEnum
from biomed_ontology.parse import parse_document

DEFAULT_RAW = Path("data/raw")
DEFAULT_OUT = Path("data/corpus/parsed")
DEFAULT_ASSETS = Path("data/assets")

# CC BY 是可再分发的最宽松档，对应最低的许可等级；本脚本只收 CC BY 语料。
_TIER = {"CC BY": LicenseTierEnum.TIER_0}


def run(raw_dir: Path, out_dir: Path, assets_dir: Path, source_id: str) -> int:
    manifest = raw_dir / "corpus.json"
    if not manifest.is_file():
        print(f"缺少 {manifest}，先跑 scripts/fetch_corpus.py", file=sys.stderr)
        return 2

    records = json.loads(manifest.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    total_images = 0

    for rec in records:
        doc_id = rec["doc_id"]
        pdf = raw_dir / rec["pdf"]
        tier = _TIER.get(rec.get("license", ""))
        if tier is None:
            print(f"  跳过 {doc_id}：许可 {rec.get('license')!r} 未登记等级", file=sys.stderr)
            failures += 1
            continue
        if not pdf.is_file():
            print(f"  跳过 {doc_id}：{pdf} 不存在", file=sys.stderr)
            failures += 1
            continue

        safe = doc_id.replace(":", "_").replace("/", "_")
        try:
            parsed = parse_document(
                pdf,
                doc_id=doc_id,
                source_id=source_id,
                title=rec.get("title") or doc_id,
                doc_type=DocTypeEnum.JOURNAL_ARTICLE,
                license_tier=tier,
                language=LanguageEnum.en,
                external_id=doc_id.removeprefix("DOC:"),
                out_dir=assets_dir / safe,
            )
        except Exception as exc:  # 一篇解析失败不该让另外八篇白跑
            failures += 1
            print(f"  FAIL  {doc_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        (out_dir / f"{safe}.yaml").write_text(
            yaml.safe_dump(parsed.to_yaml_obj(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        doc = parsed.document
        total_images += len(doc.images)
        print(
            f"  ok    {doc_id}  章节 {len(doc.sections)}"
            f"  表 {len(doc.tables)}  图 {len(doc.images)}"
        )

    print(f"\n完成：{len(records) - failures} 篇成功，{failures} 篇失败，共 {total_images} 张图。")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="批量解析已下载的语料")
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--source-id", default="PUBMED")
    args = parser.parse_args()
    return run(args.raw, args.out, args.assets, args.source_id)


if __name__ == "__main__":
    raise SystemExit(main())
