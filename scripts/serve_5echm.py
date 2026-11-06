#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在本地运行《5e 不全书》网页版，并使用本地全文搜索。

官方静态站（5echm-web/，来自 DND5eChm/5echm_web 的 pages 分支）自带的搜索框
会请求在线后端 https://5echmsearch.kagangtuya.top。本脚本在提供静态文件的
同时，把 webhelpsearch.htm 的 API_BASE 改写为同源，并实现 /api/search、
/api/filters，使整站（含搜索）完全离线。

索引来自 5echm-web/topics/ 下全部内容页，缓存于 anna 的 .cache/5echm-search.pkl；
静态站 git 提交变化时自动重建。

一般通过 Makefile 使用（make serve / make index，make help 查看全部），
也可直接运行本脚本，参数见 --help。
"""

from __future__ import annotations

import argparse
import html
import json
import math
import pickle
import re
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from paths import WEB, CACHE

# 官方网页版使用的在线搜索后端；仅在改写页面时作为匹配对象出现。
REMOTE_API = "https://5echmsearch.kagangtuya.top"

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


def build_index(force: bool = False, quiet: bool = False) -> list[dict]:
    if not (WEB / "topics").is_dir():
        sys.exit(f"找不到 {WEB}，请先运行 make clone 下载网页版静态站")

    commit = web_commit()
    if not force and commit != "unknown" and CACHE.exists():
        try:
            data = pickle.loads(CACHE.read_bytes())
            if data.get("source") == str(WEB.resolve()) and data.get("commit") == commit and data.get("entries"):
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

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(
        pickle.dumps({"source": str(WEB.resolve()), "commit": commit, "built": time.time(), "entries": entries},
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
    if not hits and len(terms) > 1:
        # 多词查询无全命中时退回“任一词命中”
        hits = collect(require_all=False)
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
        "total": total,
        "page": page,
        "totalPages": math.ceil(total / page_size) if total else 0,
        "pageSize": page_size,
    }


def build_filters(entries: list[dict]) -> dict:
    categories = sorted({entry["cat"] for entry in entries})
    return {"categories": categories, "defaultCategory": "未分类"}


def build_patched_search_page() -> bytes:
    path = WEB / "webhelpsearch.htm"
    raw = read_text(path)
    patched, count_api = re.subn(
        r'var\s+API_BASE\s*=\s*"' + re.escape(REMOTE_API) + r'"',
        'var API_BASE = ""',
        raw,
    )
    patched, _ = re.subn(
        r"后端服务未启动，请运行\s*`node server\.js`",
        "本地搜索服务未启动，请运行 make serve",
        patched,
    )
    patched, _ = re.subn(
        r"后端已运行\s*`node server\.js`",
        "本地服务已运行（make serve）",
        patched,
    )
    if count_api != 1:
        print(
            "[搜索] 警告：webhelpsearch.htm 的 API_BASE 改写未命中，"
            "搜索可能仍指向在线后端；请检查静态站是否改版。",
            file=sys.stderr,
        )
    return patched.encode("utf-8")


class Handler(SimpleHTTPRequestHandler):
    entries: list[dict] = []
    patched_search_page: bytes = b""
    quiet = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt, *args):  # noqa: D102
        if not self.quiet:
            super().log_message(fmt, *args)

    def do_GET(self):  # noqa: N802
        parsed = urlsplit(self.path)
        route = unquote(parsed.path)
        if route == "/api/search":
            params = parse_qs(parsed.query, keep_blank_values=True)
            self._send_json(search_entries(self.entries, params))
            return
        if route == "/api/filters":
            self._send_json(build_filters(self.entries))
            return
        if route == "/webhelpsearch.htm":
            self._send_bytes(self.patched_search_page, "text/html; charset=utf-8")
            return
        super().do_GET()

    def _send_json(self, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send_bytes(body, "application/json; charset=utf-8")

    def _send_bytes(self, body: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="本地运行 5e不全书 网页版（含本地全文搜索）"
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--rebuild", action="store_true", help="忽略缓存重建索引")
    parser.add_argument(
        "--build-index", action="store_true", help="只构建索引后退出（供 make index）"
    )
    parser.add_argument("--verbose", action="store_true", help="输出每条 HTTP 访问日志")
    args = parser.parse_args()

    entries = build_index(force=args.rebuild, quiet=args.build_index)
    if args.build_index:
        print(
            f"[搜索] 索引已保存到 {CACHE}（{len(entries)} 页）",
            flush=True,
        )
        return

    Handler.entries = entries
    Handler.patched_search_page = build_patched_search_page()
    Handler.quiet = not args.verbose

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"[5e不全书] 本地网页版: http://{host}:{args.port}/", flush=True)
    print("[5e不全书] 搜索已切换为本地索引（离线可用）；Ctrl-C 停止", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[5e不全书] 已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
