#!/usr/bin/env python3
"""
lit_review.py — 文献调研"滚雪球"脚本

从一组"种子论文"出发，抓取关联文献、补全元数据、去重，输出 Zotero
可直接导入的 BibTeX。

设计要点（对应工作流优化）：
  - 关联文献：Semantic Scholar recommendations（ML 推荐，等价于
    Connected Papers 图谱）。无 key 时限速较严，强烈建议设置免费 API key；
  - 元数据补全：OpenAlex（免 key、额度宽裕、作者全名/期刊/卷期页齐全）；
  - 输出标准 .bib，交给 Zotero 自带的 BibTeX translator 导入；
  - 分类由用户一次性建好并选中目标分类后导入（不碰 Zotero 数据库）。

依赖：仅 Python 3 标准库。
可选环境变量：
  S2_API_KEY      Semantic Scholar API key（免费，提升限速）
                  https://www.semanticscholar.org/product/api#api-key-form
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

S2_BASE = "https://api.semanticscholar.org/graph/v1"
OPENALEX_BASE = "https://api.openalex.org"
USER_AGENT = "lit-review-skill/1.0 (mailto:you@example.com)"
S2_FIELDS = "paperId,title,year,authors,externalIds,venue,abstract"

_rate_limited = {"hit": False}


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 30, headers: dict | None = None) -> dict:
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def s2_get(path: str, params: dict | None = None, retries: int = 6) -> dict:
    """Semantic Scholar GET，带限速退避重试（含"响应体内 429"的情况）。"""
    url = S2_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": USER_AGENT}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            body = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            if e.code == 429:
                _rate_limited["hit"] = True
                time.sleep(min(2 ** attempt * 3, 45))
                continue
            raise
        except urllib.error.URLError:
            time.sleep(2 ** attempt)
            continue
        if body.get("code") == "429" or "Too Many Requests" in body.get("message", ""):
            _rate_limited["hit"] = True
            time.sleep(min(2 ** attempt * 3, 45))
            continue
        return body
    return {}


def openalex_work_by_doi(doi: str) -> dict | None:
    try:
        return _get_json(f"{OPENALEX_BASE}/works/doi:{urllib.parse.quote(doi)}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 论文解析
# ---------------------------------------------------------------------------

_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)\b")


def resolve_seed(query: str) -> dict | None:
    """把一条 seed（标题 / DOI / arXiv ID / URL）解析成 Semantic Scholar 论文对象。"""
    q = query.strip()
    if not q or q.startswith("#"):
        return None
    m = _ARXIV_RE.fullmatch(q) or _ARXIV_RE.search(q)
    if m:
        paper = s2_get(f"/paper/arXiv:{m.group(1)}", {"fields": S2_FIELDS})
        if paper.get("paperId"):
            return paper
    m = _DOI_RE.search(q)
    if m:
        paper = s2_get(f"/paper/DOI:{urllib.parse.quote(m.group(1))}",
                       {"fields": S2_FIELDS})
        if paper.get("paperId"):
            return paper
    paper = s2_get("/paper/search/match", {"query": q, "fields": S2_FIELDS})
    if paper.get("paperId"):
        return paper
    results = s2_get("/paper/search",
                     {"query": q, "fields": S2_FIELDS, "limit": 1})
    data = results.get("data") or []
    return data[0] if data else None


def get_related(paper_id: str, limit: int = 50) -> list[dict]:
    r = s2_get(f"/paper/{paper_id}/recommendations",
               {"fields": S2_FIELDS, "limit": limit})
    return r.get("data") or []


# ---------------------------------------------------------------------------
# OpenAlex（免 key 备用源）
# ---------------------------------------------------------------------------

def _oa_work_to_entry(w: dict) -> dict:
    ext = {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    if doi:
        ext["DOI"] = doi
    for loc in w.get("locations") or []:
        if (loc.get("pdf_url") or "").startswith("https://arxiv.org"):
            m = _ARXIV_RE.search(loc["pdf_url"])
            if m:
                ext["ArXiv"] = m.group(1)
                break
    authors = [a.get("author", {}).get("display_name", "")
               for a in w.get("authorships", [])
               if a.get("author", {}).get("display_name")]
    src = w.get("primary_location") or {}
    venue = (src.get("source") or {}).get("display_name") or ""
    biblio = w.get("biblio") or {}
    return {
        "title": w.get("display_name") or "(no title)",
        "year": str(w.get("publication_year") or ""),
        "authors": authors,
        "venue": venue,
        "externalIds": ext,
        "type": w.get("type"),
        "volume": biblio.get("volume"),
        "page": biblio.get("first_page"),
        "openalex_id": w.get("id"),
    }


def openalex_resolve(query: str) -> dict | None:
    q = query.strip()
    if not q or q.startswith("#"):
        return None
    # arXiv ID → 构造成 arXiv DOI（OpenAlex 索引了 10.48550/arXiv.* ）
    m = _ARXIV_RE.fullmatch(q) or _ARXIV_RE.search(q)
    if m:
        w = openalex_work_by_doi(f"10.48550/arXiv.{m.group(1)}")
        if w and w.get("id"):
            return _oa_work_to_entry(w)
    m = _DOI_RE.search(q)
    if m:
        w = openalex_work_by_doi(m.group(1))
        if w and w.get("id"):
            return _oa_work_to_entry(w)
    # 按标题搜索
    try:
        url = f"{OPENALEX_BASE}/works?" + urllib.parse.urlencode(
            {"filter": f"title.search:{q}", "per-page": "1"})
        r = _get_json(url)
        results = r.get("results") or []
        return _oa_work_to_entry(results[0]) if results else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 元数据补全（OpenAlex）
# ---------------------------------------------------------------------------

def enrich_by_openalex(entries: list[dict]) -> None:
    """用 OpenAlex 按 DOI 补全：作者全名、期刊/会议、卷期页。就地修改。"""
    n = 0
    for e in entries:
        doi = (e.get("externalIds") or {}).get("DOI")
        if not doi:
            continue
        w = openalex_work_by_doi(doi)
        if not w:
            time.sleep(0.2)
            continue
        if not e.get("authors"):
            e["authors"] = [a.get("author", {}).get("display_name", "")
                            for a in w.get("authorships", [])
                            if a.get("author", {}).get("display_name")]
        if not e.get("venue"):
            src = w.get("primary_location") or {}
            e["venue"] = (src.get("source") or {}).get("display_name") or e.get("venue")
        if not e.get("year") and w.get("publication_year"):
            e["year"] = str(w["publication_year"])
        biblio = w.get("biblio") or {}
        if not e.get("volume") and biblio.get("volume"):
            e["volume"] = biblio["volume"]
        if not e.get("page") and biblio.get("first_page"):
            e["page"] = biblio["first_page"]
        e["type"] = w.get("type")  # journal-article / proceedings-article ...
        n += 1
        time.sleep(0.2)  # OpenAlex 礼貌限速（10 req/s 上限内）
    print(f"  已用 OpenAlex 补全 {n} 条元数据")


# ---------------------------------------------------------------------------
# BibTeX 输出
# ---------------------------------------------------------------------------

_BIB_ESCAPES = {"\\": r"\\", "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&",
                "$": r"\$", "#": r"\#", "_": r"\_", "~": r"\~{}", "^": r"\^{}"}


def _bib_escape(text: str) -> str:
    return "".join(_BIB_ESCAPES.get(ch, ch) for ch in (text or ""))


def _bib_key(entry: dict, used: set[str]) -> str:
    author = (entry.get("authors") or [""])[0]
    last = author.split()[-1] if author and author.split() else "paper"
    last = re.sub(r"[^a-zA-Z]", "", last).lower()
    year = str(entry.get("year") or "????")
    title = entry.get("title") or ""
    word = re.sub(r"[^a-zA-Z]", "", title.split()[0] if title else "paper").lower()
    base = f"{last}{year}{word}"[:60] or "paper"
    key, n = base, 1
    while key in used:
        n += 1
        key = f"{base}{n}"
    used.add(key)
    return key


def _authors_field(entry: dict) -> str:
    return " and ".join(_bib_escape(a) for a in (entry.get("authors") or []) if a)


def entry_to_bibtex(entry: dict, used: set[str]) -> str:
    key = _bib_key(entry, used)
    ext = entry.get("externalIds") or {}
    arxiv_id = ext.get("ArXiv")
    doi = ext.get("DOI") or entry.get("doi")
    # arXiv 的 DOI（10.48550/...）本质仍是预印本，按 arXiv 处理
    arxiv_doi = bool(doi) and doi.lower().startswith("10.48550")
    is_arxiv_only = bool(arxiv_id) and (not doi or arxiv_doi)
    if arxiv_doi:
        doi = None
    venue = entry.get("venue") or ""
    title = _bib_escape(entry.get("title") or "(no title)")
    authors = _authors_field(entry)
    year = re.sub(r"[^0-9]", "", str(entry.get("year") or ""))[:4]

    if is_arxiv_only:
        lines = [f"@misc{{{key},", f"  title = {{{title}}},"]
        if authors:
            lines.append(f"  author = {{{authors}}},")
        if year:
            lines.append(f"  year = {{{year}}},")
        lines += [f"  eprint = {{{arxiv_id}}},",
                  "  archivePrefix = {arXiv},",
                  "  note = {Exported by lit-review},",
                  "}"]
        return "\n".join(lines)

    etype = entry.get("type", "")
    is_conf = etype == "proceedings-article" or re.search(
        r"conference|symposium|proceedings|workshop|AAAI|ACL|EMNLP|NeurIPS|ICLR|ICML|CHI",
        venue, re.I)
    tag = "inproceedings" if is_conf else "article"
    lines = [f"@{tag}{{{key},", f"  title = {{{title}}},"]
    if authors:
        lines.append(f"  author = {{{authors}}},")
    if venue:
        field = "booktitle" if tag == "inproceedings" else "journal"
        lines.append(f"  {field} = {{{_bib_escape(venue)}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if entry.get("volume"):
        lines.append(f"  volume = {{{entry['volume']}}},")
    if entry.get("page"):
        lines.append(f"  pages = {{{entry['page']}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 写入 Zotero（可选，依赖 pyzotero）
# ---------------------------------------------------------------------------

def _split_author(name: str) -> tuple[str, str]:
    name = (name or "").strip()
    if not name:
        return "", ""
    parts = name.rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])


def entry_to_zotero_item(entry: dict) -> dict:
    """把内部条目转成 Zotero Web API 的 item 模板。"""
    ext = entry.get("externalIds") or {}
    arxiv_id = ext.get("ArXiv")
    doi = ext.get("DOI") or entry.get("doi")
    if doi and doi.lower().startswith("10.48550"):
        doi = None
    is_arxiv_only = bool(arxiv_id) and not doi
    venue = entry.get("venue") or ""
    etype = entry.get("type") or ""
    is_conf = etype == "proceedings-article" or re.search(
        r"conference|symposium|proceedings|workshop|AAAI|ACL|EMNLP|NeurIPS|ICLR|ICML|CHI",
        venue, re.I)

    creators = []
    for a in (entry.get("authors") or []):
        fn, ln = _split_author(a)
        if ln:
            creators.append({"creatorType": "author", "firstName": fn, "lastName": ln})

    if is_arxiv_only:
        item = {"itemType": "preprint", "repository": "arXiv"}
        if arxiv_id:
            item["archiveID"] = arxiv_id
            item["extra"] = "arXiv: " + arxiv_id
    elif is_conf:
        item = {"itemType": "conferencePaper"}
        if venue:
            item["conferenceName"] = venue
    else:
        item = {"itemType": "journalArticle"}
        if venue:
            item["publicationTitle"] = venue

    item["title"] = entry.get("title") or "(no title)"
    if creators:
        item["creators"] = creators
    if entry.get("abstract"):
        item["abstractNote"] = entry["abstract"]
    year = re.sub(r"[^0-9]", "", str(entry.get("year") or ""))[:4]
    if year:
        item["date"] = year
    if doi:
        item["DOI"] = doi
    if entry.get("volume"):
        item["volume"] = entry["volume"]
    if entry.get("page"):
        item["pages"] = entry["page"]
    # url：优先 arXiv / DOI
    if arxiv_id:
        item["url"] = f"https://arxiv.org/abs/{arxiv_id}"
    elif doi:
        item["url"] = f"https://doi.org/{doi}"
    return item


def _collection_field(c: dict, field: str):
    if not isinstance(c, dict):
        return None
    if field in c:
        return c[field]
    d = c.get("data")
    if isinstance(d, dict):
        return d.get(field)
    return None


def _ensure_collection(zot, path: str):
    parts = [p.strip() for p in (path or "").split("/") if p.strip()]
    if not parts:
        return None
    try:
        cols = zot.collections()
    except Exception:
        cols = []
    parent_key = False  # False == 顶层
    for name in parts:
        found = None
        for c in cols:
            if (_collection_field(c, "name") == name
                    and _collection_field(c, "parentCollection") in (parent_key, None, False)):
                found = c.get("key") if isinstance(c, dict) else None
                break
        if not found:
            data = [{"name": name}]
            if parent_key:
                data[0]["parentCollection"] = parent_key
            resp = zot.create_collections(data)
            created = {}
            if isinstance(resp, dict):
                for bucket in ("successful", "success"):
                    if resp.get(bucket):
                        created.update(resp[bucket])
            vals = list(created.values())
            if vals and isinstance(vals[0], dict):
                found = vals[0].get("key")
            else:
                found = list(created.keys())[0] if created else None
        if not found:
            print(f"  创建分类 '{name}' 失败")
            return None
        parent_key = found
    return parent_key


def import_to_zotero(entries, collection_path, api_key, library_id, library_type) -> int:
    try:
        from pyzotero import zotero as pz
    except ImportError:
        print("  需要 pyzotero 库：pip install pyzotero")
        return 0
    zot = pz.Zotero(library_id, library_type, api_key)
    templates = [entry_to_zotero_item(e) for e in entries]
    resp = zot.create_items(templates)
    created = []
    if isinstance(resp, dict):
        for bucket in ("successful", "success"):
            if resp.get(bucket):
                created += list(resp[bucket].values())
    elif isinstance(resp, list):
        created = resp
    if not created:
        failed = resp.get("failed", {}) if isinstance(resp, dict) else {}
        print(f"  写入失败：{len(failed)} 条")
        return 0
    print(f"  已创建条目 {len(created)} 条")
    if collection_path:
        col_key = _ensure_collection(zot, collection_path)
        if col_key:
            ok = 0
            for it in created:
                key = it.get("key") if isinstance(it, dict) else None
                if not key:
                    continue
                try:
                    zot.addto_collection(col_key, key)
                    ok += 1
                except Exception:
                    pass
            print(f"  已加入分类 '{collection_path}'：{ok} 条")
    return len(created)


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------

def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def dedup(entries: list[dict]) -> tuple[list[dict], list[str]]:
    seen_doi, seen_arxiv, seen_title = {}, {}, {}
    report, unique = [], []
    for e in entries:
        ext = e.get("externalIds") or {}
        doi, arxiv = ext.get("DOI"), ext.get("ArXiv")
        title = _norm_title(e.get("title") or "")
        dup_of = None
        if doi and doi in seen_doi:
            dup_of = seen_doi[doi]
        elif arxiv and arxiv in seen_arxiv:
            dup_of = seen_arxiv[arxiv]
        elif title and title in seen_title:
            dup_of = seen_title[title]
        if dup_of is not None:
            report.append(f"  去重: \"{e.get('title','')}\" 与 #{dup_of} 重复")
            continue
        idx = len(unique)
        unique.append(e)
        if doi:
            seen_doi[doi] = idx
        if arxiv:
            seen_arxiv[arxiv] = idx
        if title:
            seen_title[title] = idx
    return unique, report


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="文献滚雪球：种子论文 → 关联文献 → BibTeX")
    ap.add_argument("seeds", help="种子文件，每行一个标题/DOI/arXiv ID")
    ap.add_argument("--related-limit", type=int, default=50, help="每篇种子的关联文献数上限")
    ap.add_argument("--out", default="lit-review-output", help="输出目录")
    ap.add_argument("--enrich", action="store_true", help="用 OpenAlex 补全作者/期刊/卷期页")
    ap.add_argument("--per-seed", action="store_true", help="额外为每篇种子生成一个 .bib")
    ap.add_argument("--source", default="auto",
                    choices=["auto", "semanticscholar", "openalex"],
                    help="数据源：semanticscholar（推荐，需 S2_API_KEY）/ openalex（免 key）/ auto（自动降级）")
    ap.add_argument("--to-zotero", action="store_true",
                    help="把结果写入 Zotero 库（需 ZOTERO_API_KEY 与 ZOTERO_LIBRARY_ID 环境变量，需 pip install pyzotero）")
    ap.add_argument("--zotero-collection", default=None,
                    help="写入的分类，如 'proactive agent/more'（不存在会自动创建）")
    ap.add_argument("--zotero-library-type", default="user",
                    help="Zotero 库类型：user 或 group")
    args = ap.parse_args()

    with open(args.seeds, encoding="utf-8") as f:
        seeds = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]

    os.makedirs(args.out, exist_ok=True)
    seed_meta, related_all = [], []
    print(f"共 {len(seeds)} 篇种子论文，数据源: {args.source}\n")

    for i, seed in enumerate(seeds, 1):
        paper, rel = None, []
        src_used = args.source

        if args.source in ("semanticscholar", "auto"):
            paper = resolve_seed(seed)
            if paper:
                rel = get_related(paper["paperId"], args.related_limit)
                if not os.environ.get("S2_API_KEY"):
                    time.sleep(1.1)
            if paper and rel:
                src_used = "semanticscholar"
            elif args.source == "auto":
                # Semantic Scholar 限速/无结果时降级到 OpenAlex（仅解析，关联文献仍走 S2）
                paper = openalex_resolve(seed)
                src_used = "openalex"
        else:  # openalex
            paper = openalex_resolve(seed)
            src_used = "openalex"

        if not paper:
            print(f"[{i}/{len(seeds)}] 未解析: {seed}")
            continue
        ext = paper.get("externalIds") or {}
        print(f"[{i}/{len(seeds)}] {paper.get('title','')[:60]}  "
              f"(year={paper.get('year')}, arXiv={ext.get('ArXiv','-')}) [{src_used}]")
        seed_meta.append(paper)
        paper["_related"] = rel
        related_all.extend(rel)
        print(f"    关联文献 {len(rel)} 篇")

    if _rate_limited["hit"]:
        print("\n⚠ 触发了 Semantic Scholar 限速。强烈建议设置免费 API key 提升稳定性：")
        print("  export S2_API_KEY=你的key   (申请: https://www.semanticscholar.org/product/api#api-key-form)")

    if not related_all:
        print("未抓取到任何关联文献。关联文献依赖 Semantic Scholar，请设置 S2_API_KEY 后重试。")
        return 1

    unique, report = dedup(related_all)
    print(f"\n去重前 {len(related_all)} 篇，去重后 {len(unique)} 篇")
    for line in report:
        print(line)

    if args.enrich:
        print("\n补全元数据（OpenAlex）...")
        enrich_by_openalex(unique)

    used_keys: set[str] = set()
    all_bib = "\n\n".join(entry_to_bibtex(e, used_keys) for e in unique) + "\n"
    with open(os.path.join(args.out, "all.bib"), "w", encoding="utf-8") as f:
        f.write(all_bib)

    if args.to_zotero:
        api_key = os.environ.get("ZOTERO_API_KEY")
        library_id = os.environ.get("ZOTERO_LIBRARY_ID")
        if not api_key or not library_id:
            print("\n⚠ --to-zotero 需要环境变量：")
            print("  export ZOTERO_API_KEY=你的key")
            print("  export ZOTERO_LIBRARY_ID=你的userID   (申请: https://www.zotero.org/settings/keys)")
        else:
            print("\n写入 Zotero 库...")
            import_to_zotero(unique, args.zotero_collection,
                             api_key, library_id, args.zotero_library_type)

    if args.per_seed:
        for si, paper in enumerate(seed_meta):
            rel = paper.get("_related") or []
            rel_u, _ = dedup(rel)
            used = set()
            bib = "\n\n".join(entry_to_bibtex(e, used) for e in rel_u) + "\n"
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", paper.get("title", "seed"))[:50].strip("-")
            with open(os.path.join(args.out, f"{si+1:02d}-{slug}.bib"),
                      "w", encoding="utf-8") as f:
                f.write(bib)

    with open(os.path.join(args.out, "seeds.json"), "w", encoding="utf-8") as f:
        json.dump(seed_meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "related.json"), "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    print(f"\n完成。输出目录: {args.out}")
    print(f"  all.bib ({len(unique)} 条)  → 拖入 Zotero 即可导入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
