#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local full-text search over a read-only source snapshot; no upstream UI server.

The reading app loads the pre-indexed book_search table from catalog.sqlite when it
matches the source fingerprint, and falls back to scanning the source directory.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import html
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from anna.paths import PROJECT, WEB

SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
BODY_RE = re.compile(r"(?is)<body[^>]*>(.*)</body>")
TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
BLOCK_RE = re.compile(
    r"(?i)<(?:br|/p|/div|/h[1-6]|/li|/tr|/table|/section|/ul|/ol|/blockquote)\b[^>]*>"
)
TAG_RE = re.compile(r"(?s)<[^>]+>")
ANCHOR_RE = re.compile(r'(?is)<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>')
TERM_RE = re.compile(r'"([^"]+)"|(\S+)')


def read_text(path: Path) -> str:
    return path.read_text("utf-8", errors="replace")


def strip_tags(fragment: str) -> str:
    return html.unescape(TAG_RE.sub("", fragment)).strip()


def extract_title(raw: str) -> str:
    match = TITLE_RE.search(raw)
    return strip_tags(match.group(1)) if match else ""


def extract_body_text(raw: str) -> str:
    raw = SCRIPT_RE.sub(" ", raw)
    match = BODY_RE.search(raw)
    body = match.group(1) if match else raw
    body = BLOCK_RE.sub("\n", body)
    text = html.unescape(TAG_RE.sub("", body))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_toc_titles(web: Path | None = None) -> dict[str, str]:
    """从目录页里取各页面的标题（与官方搜索返回的 title 一致）。"""
    path = (web or WEB) / "webhelpcontents.htm"
    if not path.exists():
        return {}
    titles: dict[str, str] = {}
    for href, label in ANCHOR_RE.findall(read_text(path)):
        href = unquote(href)
        if not href.lower().startswith("topics/"):
            continue
        rel = href[len("topics/"):]
        title = strip_tags(label)
        if title and rel not in titles:
            titles[rel] = title
    return titles


def web_commit(web: Path | None = None) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(web or WEB), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception:
        return "unknown"


def index_signature(web: Path | None = None):
    """A Git commit alone cannot identify a locally changed source snapshot."""
    root = web or WEB
    digest = sha256()
    files = [p for p in (root / 'topics').rglob('*') if p.is_file() and p.suffix.lower() in {'.htm', '.html'}]
    toc = root / 'webhelpcontents.htm'
    if toc.is_file():
        files.append(toc)
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def scan_entries(source: Path | None = None, quiet: bool = False) -> list[dict]:
    root = Path(source).resolve() if source else WEB.resolve()
    if not (root / "topics").is_dir():
        sys.exit(f"找不到 {root}，请先运行 make clone 下载网页版静态站")
    started = time.time()
    if not quiet:
        print(f"[search] scanning {root / 'topics'} ...", flush=True)
    snapshot = index_signature(root)
    toc_titles = load_toc_titles(root)
    entries: list[dict] = []
    for path in sorted((root / "topics").rglob("*")):
        if path.suffix.lower() not in (".htm", ".html"):
            continue
        try:
            raw = read_text(path)
        except Exception:
            continue
        rel = path.relative_to(root).as_posix()
        src = rel.split("/", 1)[1] if "/" in rel else rel
        text = extract_body_text(raw)
        title = toc_titles.get(src) or extract_title(raw) or path.stem
        entries.append(
            {
                "i": len(entries),
                "url": rel,
                "src": src,
                "title": title,
                "tll": title.lower(),
                "cat": src.split("/", 1)[0] if "/" in src else src,
                "text": text,
                "tl": text.lower(),
            }
        )
    if index_signature(root) != snapshot:
        raise ValueError('Source changed while scanning search index; retry')
    if not quiet:
        chars = sum(len(e["text"]) for e in entries)
        print(
            f"[搜索] 扫描完成：{len(entries)} 页 / {chars / 1e6:.1f}M 字 / "
            f"{time.time() - started:.1f}s",
            flush=True,
        )
    return entries


def indexed_entries(db):
    """Read the published full-text index from an already validated database."""
    return [
        {"i": i, "url": url, "src": src, "title": title, "tll": title.lower(),
         "cat": cat, "text": text, "tl": text.lower()}
        for i, (url, src, title, cat, text) in enumerate(
            db.execute('SELECT url,src,title,cat,text FROM book_search ORDER BY rowid')
        )
    ]


def load_index(db_path: Path | None = None, source: Path | None = None, quiet: bool = False) -> list[dict]:
    root = Path(source).resolve() if source else WEB.resolve()
    if not (root / "topics").is_dir():
        sys.exit(f"找不到 {root}，请先运行 make clone 下载网页版静态站")
    snapshot = index_signature(root)
    if db_path and Path(db_path).exists():
        try:
            uri = Path(db_path).resolve().as_uri() + '?mode=ro'
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='book_search'").fetchone()
                sig = conn.execute("SELECT value FROM metadata WHERE key='book_search_signature'").fetchone()
                if has_table and sig and sig[0] == snapshot:
                    entries = indexed_entries(conn)
                    if entries:
                        if not quiet:
                            print(
                                f"[搜索] 已载入库内全文索引：{len(entries)} 页 (commit {web_commit(root)[:7]})",
                                flush=True,
                            )
                        return entries
        except Exception:
            pass
    return scan_entries(root, quiet=quiet)


def parse_terms(keyword: str) -> list[str]:
    terms: list[str] = []
    for match in TERM_RE.finditer(keyword):
        raw = match.group(1) or match.group(2) or ""
        for part in raw.split("|"):
            term = part.strip().lower()
            if term and term not in terms:
                terms.append(term)
    return terms


def make_preview(text: str, text_lower: str, terms: list[str]) -> str:
    pos = -1
    for term in terms:
        found = text_lower.find(term)
        if found != -1 and (pos == -1 or found < pos):
            pos = found
    if pos == -1:
        snippet = text[:600]
        return snippet + ("…" if len(text) > 600 else "")
    start = max(0, pos - 180)
    end = min(len(text), pos + 460)
    return (
        ("…" if start > 0 else "")
        + text[start:end]
        + ("…" if end < len(text) else "")
    )


def ranked_hits(entries: list[dict], terms: list[str], *, title_only: bool = False, category: str = "all",
                allowed: set[int] | None = None) -> tuple[str, list[tuple[int, int]]]:
    """Every page matching all terms, best first, or any term when none matches all.

    Returns the match mode and (score, page index) pairs. A title hit counts a thousand body hits, so scores from
    different collections ranked by the same rule compare directly."""

    def collect(require_all: bool) -> list[tuple[int, int]]:
        hits: list[tuple[int, int]] = []
        for entry in entries:
            if allowed is not None and entry["i"] not in allowed:
                continue
            if category != "all" and entry["cat"] != category:
                continue
            title_counts = [entry["tll"].count(term) for term in terms]
            if title_only:
                counts = title_counts
            else:
                body_counts = [entry["tl"].count(term) for term in terms]
                counts = [
                    title_counts[j] * 1000 + min(body_counts[j], 200)
                    for j in range(len(terms))
                ]
            if require_all:
                if any(count == 0 for count in counts):
                    continue
            elif not any(count > 0 for count in counts):
                continue
            score = sum(counts)
            if require_all and all(c > 0 for c in title_counts) and not title_only:
                score += 500
            hits.append((score, entry["i"]))
        return hits

    hits = collect(require_all=True)
    match = 'all'
    if not hits and len(terms) > 1:
        # 多词查询无全命中时退回“任一词命中”
        hits = collect(require_all=False)
        match = 'any'
    hits.sort(key=lambda item: (-item[0], item[1]))
    return match, hits


def search_result(entry: dict, terms: list[str], score: int) -> dict:
    """One page as a search result, its preview cut around the first term found."""
    return {
        "index": entry["i"],
        "title": entry["title"],
        "rawTitle": entry["title"],
        "path": entry["url"],
        "sourcePath": entry["src"],
        "rank": score,
        "preview": make_preview(entry["text"], entry["tl"], terms),
        "category": entry["cat"],
    }


def search_entries(entries: list[dict], params: dict[str, list[str]]) -> dict:
    keyword = (params.get("keyword") or [""])[0].strip()
    terms = parse_terms(keyword)
    if not terms:
        return {"results": [], "total": 0, "page": 1, "totalPages": 0, "pageSize": 0}

    def first(name: str, default: str) -> str:
        return (params.get(name) or [default])[0]

    title_only = first("titleOnly", "false").lower() in ("true", "1")
    try:
        page = max(1, int(first("page", "1") or 1))
    except ValueError:
        page = 1
    try:
        page_size = min(200, max(1, int(first("pageSize", "100") or 100)))
    except ValueError:
        page_size = 100
    allowed: set[int] | None = None
    base = first("baseIndexes", "")
    if base:
        allowed = {int(x) for x in base.split(",") if x.strip().isdigit()}
    match, hits = ranked_hits(entries, terms, title_only=title_only, category=first("category", "all"), allowed=allowed)
    total = len(hits)
    start = (page - 1) * page_size
    results = [search_result(entries[index], terms, score) for score, index in hits[start:start + page_size]]
    return {
        "results": results,
        "match": match,
        "total": total,
        "page": page,
        "totalPages": math.ceil(total / page_size) if total else 0,
        "pageSize": page_size,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('keyword', nargs='?', default='', help='Search query string')
    parser.add_argument('--db', type=Path, default=PROJECT / 'data/extracted/catalog.sqlite')
    parser.add_argument('--source', type=Path, default=WEB)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--page', type=int, default=1)
    parser.add_argument('--regex', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    entries = load_index(args.db, args.source, quiet=True)
    if not args.keyword:
        print(json.dumps({'indexed_pages': len(entries)}, ensure_ascii=False))
        return
    params = {
        'q': [args.keyword],
        'pageSize': [str(max(1, min(100, args.limit)))],
        'page': [str(max(1, args.page))],
        'regex': ['1' if args.regex else '0'],
    }
    result = search_entries(entries, params)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result['total']} pages matched (showing {len(result['results'])})")
        for item in result['results']:
            print(f"{item['rawTitle']}\t{item['path']}")


if __name__ == '__main__':
    main()
