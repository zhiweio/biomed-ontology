"""下载 PoC 语料：PMC Open Access 全文 + 公开专利 PDF。

真实 PDF 不入 git —— 体积之外更要紧的是许可：仓库一旦包含第三方全文，
再分发时的合规责任就转移到了本仓库。这里只提交清单与下载脚本，
每个人在本地各自取得副本，许可关系仍停留在原始发布方与使用者之间。

安全约束（下载器是典型 SSRF 面）：
- 只允许 https，且 host 必须在白名单内；重定向后的 host 同样校验
- 落盘文件名由 doc_id 生成，绝不取自 URL 或响应头（路径穿越）
- 单文件大小上限，边下边计数，不信任 Content-Length

用法：uv run python scripts/fetch_corpus.py [--manifest ...] [--out ...]
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "data" / "raw" / "manifest.yaml"
DEFAULT_OUT = REPO_ROOT / "data" / "raw"

MAX_BYTES = 64 * 1024 * 1024
TIMEOUT = httpx.Timeout(60.0, connect=15.0)

ALLOWED_HOSTS = frozenset(
    {
        "www.ncbi.nlm.nih.gov",
        "ftp.ncbi.nlm.nih.gov",
        "www.ebi.ac.uk",
        "europepmc.org",
        "patentimages.storage.googleapis.com",
        "pdfpiw.uspto.gov",
    }
)

OA_SERVICE = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# 允许入库的许可。NC/ND 变体被刻意排除：PoC 产出可能进入商业决策流程，
# 而 NonCommercial 与它不相容 —— 这个判断要在下载阶段就做掉，不能留到出报告时。
ALLOWED_LICENSES = ("CC BY", "CC0", "CC BY-SA")


@dataclass(frozen=True)
class Entry:
    doc_id: str
    kind: str
    license: str
    pmcid: str = ""
    url: str = ""
    note: str = ""


class FetchError(RuntimeError):
    pass


def _check_host(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FetchError(f"仅允许 https：{url}")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise FetchError(f"host 不在白名单：{parsed.hostname}")
    return url


def _download(client: httpx.Client, url: str, dest: Path) -> int:
    _check_host(url)
    total = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url, follow_redirects=True) as resp:
        # 重定向链的终点也必须在白名单内，否则白名单形同虚设。
        _check_host(str(resp.url))
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(64 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    tmp.unlink(missing_ok=True)
                    raise FetchError(f"超过大小上限 {MAX_BYTES} 字节：{url}")
                fh.write(chunk)
    tmp.replace(dest)
    return total


def _resolve_pmc(client: httpx.Client, pmcid: str) -> tuple[str, str]:
    """经 OA Web Service 拿到下载地址与**服务端声明的**许可。

    许可不取清单里的自填值：清单是人写的，会过期也会写错，
    而 OA 服务返回的是 NCBI 当前的权威声明。两者不一致时以服务端为准并报错。
    """
    resp = client.get(OA_SERVICE, params={"id": pmcid}, follow_redirects=True)
    resp.raise_for_status()
    # XML 解析是放大攻击面（billion laughs）。来源虽在白名单内，仍先限长再解析。
    if len(resp.content) > 1 * 1024 * 1024:
        raise FetchError(f"{pmcid}: OA 响应过大，拒绝解析")
    root = ET.fromstring(resp.text)

    error = root.find(".//error")
    if error is not None:
        raise FetchError(f"{pmcid}: OA 服务返回 {error.get('code')} {error.text or ''}".strip())

    link = root.find(".//link[@format='pdf']") or root.find(".//link")
    if link is None or not link.get("href"):
        raise FetchError(f"{pmcid}: OA 服务未返回可下载链接")

    href = link.get("href", "")
    # OA 服务给的是 ftp:// 地址，转成同主机的 https。
    if href.startswith("ftp://"):
        href = "https://" + href.removeprefix("ftp://")

    record = root.find(".//record")
    license_ = (record.get("license") if record is not None else "") or "UNKNOWN"
    return href, license_


def _license_ok(license_: str) -> bool:
    up = license_.upper().replace("-", " ")
    if "NC" in up.split() or "NONCOMMERCIAL" in up:
        return False
    return any(allowed.upper().replace("-", " ") in up for allowed in ALLOWED_LICENSES)


def load_manifest(path: Path) -> list[Entry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Entry(**item) for item in raw.get("documents", [])]


def load_queries(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(raw.get("queries", []))


def discover(client: httpx.Client, queries: list[dict]) -> list[Entry]:
    """用 Europe PMC 检索解析出 PMCID，而不是在清单里硬编。

    硬编 ID 会随时间腐坏（撤稿、许可变更、迁移），且没人会去复核；
    检索式则把"我要哪一类文献"这个真实意图留在仓库里，每次执行都重新求值。
    """
    found: list[Entry] = []
    seen: set[str] = set()
    for q in queries:
        expr = f'({q["query"]}) AND OPEN_ACCESS:Y AND IN_EPMC:Y AND LICENSE:"cc by"'
        resp = client.get(
            EPMC_SEARCH,
            params={"query": expr, "format": "json", "pageSize": q.get("limit", 3)},
            follow_redirects=True,
        )
        resp.raise_for_status()
        for rec in resp.json().get("resultList", {}).get("result", []):
            pmcid = rec.get("pmcid")
            if not pmcid or pmcid in seen:
                continue
            seen.add(pmcid)
            found.append(
                Entry(
                    doc_id=f"DOC:{pmcid}",
                    kind="pmc",
                    license="CC BY",
                    pmcid=pmcid,
                    note=f"{q['label']} — {(rec.get('title') or '')[:80]}",
                )
            )
    return found


def fetch(manifest: Path, out_dir: Path) -> int:
    entries = load_manifest(manifest)
    queries = load_queries(manifest)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str, str]] = []
    failures = 0

    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "hmd-biomed-ontology/0.1"}) as client:
        if queries:
            try:
                discovered = discover(client, queries)
                print(f"Europe PMC 检索到 {len(discovered)} 篇 CC-BY 全文")
                entries = entries + discovered
            except httpx.HTTPError as exc:
                print(f"  FAIL  Europe PMC 检索: {exc}", file=sys.stderr)
                failures += 1

        for entry in entries:
            # 文件名只由 doc_id 派生，不碰 URL 与响应头。
            safe = entry.doc_id.replace(":", "_").replace("/", "_")
            dest = out_dir / f"{safe}.pdf"
            try:
                if entry.kind == "pmc":
                    url, declared = _resolve_pmc(client, entry.pmcid)
                    if not _license_ok(declared):
                        raise FetchError(f"许可 {declared!r} 不在允许集合，跳过")
                    if entry.license and entry.license.upper() not in declared.upper():
                        raise FetchError(f"清单声明 {entry.license!r} 与 OA 服务 {declared!r} 不符")
                else:
                    url, declared = entry.url, entry.license

                if dest.exists():
                    print(f"  skip  {entry.doc_id}  (已存在)")
                else:
                    size = _download(client, url, dest)
                    print(f"  ok    {entry.doc_id}  {size / 1024:.0f} KB  [{declared}]")
                rows.append((entry.doc_id, declared, url, entry.note))
            except (FetchError, httpx.HTTPError, ET.ParseError) as exc:
                failures += 1
                print(f"  FAIL  {entry.doc_id}: {exc}", file=sys.stderr)

    _write_sources(out_dir / "SOURCES.md", rows)
    print(f"\n完成：{len(rows)} 份成功，{failures} 份失败。清单见 {out_dir / 'SOURCES.md'}")
    return 1 if failures else 0


def _write_sources(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    lines = [
        "# 语料来源与许可",
        "",
        "本文件由 `scripts/fetch_corpus.py` 生成。PDF 本体不入版本库。",
        "",
        "| doc_id | 许可 | 来源 | 备注 |",
        "|---|---|---|---|",
    ]
    lines += [f"| `{d}` | {lic} | {url} | {note} |" for d, lic, url, note in sorted(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 PoC 语料")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"清单不存在：{args.manifest}", file=sys.stderr)
        return 2
    return fetch(args.manifest, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
