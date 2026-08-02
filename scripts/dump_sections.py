"""打印每篇文档的 section 清单，供人工编写 `data/gold/retrieval.yaml` 的判定键。

gold 的键是 `doc_id#section`，而 section 名来自解析结果 —— 凭记忆写必然拼错，
而拼错的键会被 `eval_retrieval` 的 dangling 检查拦下、整份评测拒绝出数。
这个脚本存在的意义就是让"照抄一份真实存在的键"成为默认动作。

    uv run python scripts/dump_sections.py                 # 全部 14 篇
    uv run python scripts/dump_sections.py PMC13235695     # 只看某几篇
    uv run python scripts/dump_sections.py --grep MET      # 只看正文含关键词的节
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from biomed_ontology.pipeline import build_knowledge_base


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("filters", nargs="*", help="doc_id 子串；留空则全部")
    ap.add_argument("--grep", default="", help="只列出正文包含该串的节（大小写不敏感）")
    ap.add_argument("--width", type=int, default=96, help="正文预览宽度")
    args = ap.parse_args()

    kb = build_knowledge_base()
    by_section: dict[tuple[str, str], list] = defaultdict(list)
    for chunk in kb.chunks:
        by_section[(chunk.doc_id, chunk.section)].append(chunk)

    needle = args.grep.casefold()
    total_sections = 0
    for doc in kb.documents:
        if args.filters and not any(f in doc.doc_id for f in args.filters):
            continue
        sections = [(k[1], v) for k, v in by_section.items() if k[0] == doc.doc_id]
        rows = []
        for name, chunks in sections:
            body = " ".join(c.text for c in chunks)
            if needle and needle not in body.casefold():
                continue
            modality = "/".join(sorted({c.modality.value for c in chunks}))
            preview = " ".join(body.split())[: args.width]
            rows.append(f'  "{doc.doc_id}#{name}": ')
            rows.append(f"      {len(chunks):>2} 片 [{modality}] p{chunks[0].page}  {preview}")
        if not rows:
            continue
        total_sections += len(rows) // 2
        print(
            f"\n=== {doc.doc_id}  [{doc.language.value} / {doc.doc_type.value} / "
            f"{doc.license_tier.value}]  {len(sections)} 节 / "
            f"{sum(len(v) for _, v in sections)} 片"
        )
        print(f"    {doc.title}")
        print("\n".join(rows))

    print(f"\n合计 {total_sections} 节（共 {len(kb.chunks)} 片 / {len(kb.documents)} 篇）")


if __name__ == "__main__":
    main()
