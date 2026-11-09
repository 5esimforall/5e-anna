#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local full-text search over a read-only source snapshot; no upstream UI server.

The reading app consumes search_entries. The CLI only builds the cache used by
make index / make update; starting the reader never updates the source mirror.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import html
import json
import math
import pickle
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from paths import WEB, CACHE

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


def load_toc_titles() -> dict[str, str]:
    """从目录页里取各页面的标题（与官方搜索返回的 title 一致）。"""
    path = WEB / "webhelpcontents.htm"
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


def web_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(WEB), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception:
        return "unknown"


def index_signature():
    """A Git commit alone cannot identify a locally changed source snapshot."""
    digest = sha256()
    files = [p for p in (WEB / 'topics').rglob('*') if p.is_file() and p.suffix.lower() in {'.htm', '.html'}]
    toc = WEB / 'webhelpcontents.htm'
    if toc.is_file():
        files.append(toc)
    for path in sorted(files):
        digest.update(path.relative_to(WEB).as_posix().encode() + b'\0')
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_index(force: bool = False, quiet: bool = False) -> list[dict]:
    if not (WEB / "topics").is_dir():
        sys.exit(f"找不到 {WEB}，请先运行 make clone 下载网页版静态站")

    commit = web_commit()
    snapshot = index_signature()
    if not force and CACHE.exists():
        try:
            data = pickle.loads(CACHE.read_bytes())
            if data.get("source") == str(WEB.resolve()) and data.get("snapshot") == snapshot and data.get("entries"):
                if not quiet:
                    print(
                        f"[搜索] 已载入缓存索引：{len(data['entries'])} 页 "
                        f"(commit {commit[:7]})",
                        flush=True,
                    )
                return data["entries"]
        except Exception:
            pass

    started = time.time()
    if not quiet:
        print(f"[搜索] 构建索引：扫描 {WEB / 'topics'} ...", flush=True)
    toc_titles = load_toc_titles()
    entries: list[dict] = []
    for path in sorted((WEB / "topics").rglob("*")):
        if path.suffix.lower() not in (".htm", ".html"):
            continue
        try:
            raw = read_text(path)
        except Exception:
            continue
        rel = path.relative_to(WEB).as_posix()
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

    if not quiet:
        chars = sum(len(e["text"]) for e in entries)
        print(
            f"[搜索] 索引完成：{len(entries)} 页 / {chars / 1e6:.1f}M 字 / "
            f"{time.time() - started:.1f}s",
            flush=True,
        )

    if index_signature() != snapshot:
        raise ValueError('原文在索引期间变化，请重试 make index')
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(
        pickle.dumps({"source": str(WEB.resolve()), "commit": commit, "snapshot": snapshot, "built": time.time(), "entries": entries},
                     protocol=pickle.HIGHEST_PROTOCOL)
    )
    return entries


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


def search_entries(entries: list[dict], params: dict[str, list[str]]) -> dict:
    keyword = (params.get("keyword") or [""])[0].strip()
    terms = parse_terms(keyword)
    if not terms:
        return {"results": [], "total": 0, "page": 1, "totalPages": 0, "pageSize": 0}

    def first(name: str, default: str) -> str:
        return (params.get(name) or [default])[0]

    title_only = first("titleOnly", "false").lower() in ("true", "1")
    category = first("category", "all")
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

    total = len(hits)
    start = (page - 1) * page_size
    results = []
    for score, index in hits[start:start + page_size]:
        entry = entries[index]
        results.append(
            {
                "index": entry["i"],
                "title": entry["title"],
                "rawTitle": entry["title"],
                "path": entry["url"],
                "sourcePath": entry["src"],
                "rank": score,
                "preview": make_preview(entry["text"], entry["tl"], terms),
                "category": entry["cat"],
            }
        )
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
    parser.add_argument('--rebuild', action='store_true', help='忽略缓存重建索引')
    args = parser.parse_args()
    entries = build_index(force=args.rebuild, quiet=True)
    print(f'[搜索] 索引已保存到 {CACHE}（{len(entries)} 页）', flush=True)


if __name__ == '__main__':
    main()
