"""Domain-free HTML parsing, bilingual name segmentation, and page layout IR."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
import posixpath
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag

BLOCKS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'li', 'ul', 'ol', 'blockquote', 'table', 'section', 'hr'}
HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
IGNORED = {'script', 'style', 'noscript', 'head'}
EXAMPLE_CLASSES = {'example', 'ex-right'}
LEGACY = re.compile(r'Legacy\b|[（(]旧版[）)]', re.I)
LOCAL_LEGACY_RE = re.compile(r'[（(](?:旧版|Legacy)[）)]', re.I)
MAROON_COLORS = {'#800000', '800000', '#ff0000', 'red', 'maroon'}
MAROON_STYLE_RE = re.compile(r'color\s*:\s*(?:rgb\(128,\s*0,\s*0\)|#800000)')
COLOR_STYLE_RE = re.compile(r'(?<![a-z-])color\s*:\s*([^;"\']+)', re.I)
PURE_ASCII_TITLE_RE = re.compile(r"[A-Za-z0-9\s'’\-:：]+")


def _is_maroon(tag: Tag) -> bool:
    color = tag.get('color', '').strip().lower()
    style = tag.get('style', '').lower()
    return color in MAROON_COLORS or bool(MAROON_STYLE_RE.search(style))


def _is_non_maroon_color(tag: Tag) -> bool:
    if tag.name not in {'font', 'span'} or _is_maroon(tag):
        return False
    return bool(tag.get('color', '').strip()) or bool(COLOR_STYLE_RE.search(tag.get('style', '')))


def _trim_span_end(text: str, a: int, b: int) -> int:
    while b > a and text[b - 1].isspace():
        b -= 1
    return b

NOTATION_JOINER = r'(?:或|、|/|／|和)'
JOINERS = ('或', '/', '／', '、')
# A conjunction counts as a separator only right after an English run and before the next
# Chinese name, so 或 inside a Chinese name (微型或小型) never splits it.
ALTERNATIVE = re.compile(r'(?<=[A-Za-z])(\s*(?:' + '|'.join(map(re.escape, JOINERS)) + r')\s*)(?=[㐀-鿿\d])')
# Without a conjunction, a translation can run straight into the next Chinese name.
# Only an English word run marks that boundary; initialisms and codes belong to the Chinese name.
ADJACENT = re.compile(r'(?<=[A-Za-z])(\s*)(?=[㐀-鿿])(?!' + '|'.join(map(re.escape, JOINERS)) + ')')
SENTENCE = re.compile(r'[，。？！；?]')
LEVEL_LABEL = re.compile(r'\s*(\d+)\s*级\s*[:：]\s*')
HAN_RE = re.compile(r'[\u3400-\u9fff]')
DICE_OR_RANGE_RE = re.compile(r'(?:\d+)?[dD]\d+(?:\s*[-+]\s*\d+)?|\d+(?:[-–~～]\d+)?')


def compact(s: object) -> str:
    return re.sub(r'\s+', ' ', str(s)).strip()


LIST_PUNCT_RE = re.compile(r'[\s，、；;,.·/／]+')


def underline_style(tag: Tag) -> str:
    """Uniform typographical emphasis around a <u> tag: bold, inner_italic, outer_italic, plain, mixed, or empty."""
    all_styles = set()
    content_styles = set()
    for node in tag.find_all(string=True):
        text = str(node)
        if not text.strip():
            continue
        ancestors = list(node.parents)
        boundary = next(i for i, p in enumerate(ancestors) if p is tag)
        inner = any(p.name in {'em', 'i'} for p in ancestors[:boundary])
        outer = any(p.name in {'em', 'i'} for p in ancestors[boundary + 1:])
        bold = any(p.name in {'b', 'strong'} for p in ancestors)
        style = (
            'mixed' if sum((inner, outer, bold)) > 1
            else 'bold' if bold
            else 'inner_italic' if inner
            else 'outer_italic' if outer
            else 'plain'
        )
        all_styles.add(style)
        if not LIST_PUNCT_RE.fullmatch(text):
            content_styles.add(style)
    if not all_styles:
        return 'empty'
    styles = content_styles or all_styles
    return styles.pop() if len(styles) == 1 else 'mixed'


def format_tag_paths(tag: Tag) -> list[list[str]]:
    paths: list[list[str]] = []
    for node in tag.find_all(string=True):
        if str(node).strip():
            path = [p.name for p in reversed(list(node.parents)) if p.name in {'u', 'em', 'i', 'b', 'strong'}]
            if path not in paths:
                paths.append(path)
    return paths


def key(s: str) -> str:
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', s).casefold())


def digest(*parts: object) -> str:
    return sha256('\0'.join(str(x) for x in parts).encode()).hexdigest()[:24]


def resolve_link(page: str, href: str) -> tuple[str, str] | None:
    """Decode once, preserve fragment spelling; reject remote/root-escaping URLs."""
    u = urlsplit(href.strip())
    if u.scheme or u.netloc:
        return None
    path = unquote(u.path)
    if not path:
        target = page
    elif path.startswith('/'):
        target = posixpath.normpath(path.lstrip('/'))
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(page), path.replace('\\', '/')))
    if target.startswith('../') or target == '..':
        return None
    return target, unquote(u.fragment)


def _has_han(text: str) -> bool:
    return bool(HAN_RE.search(text))


def _is_latin(ch: str) -> bool:
    return ch.isalpha() and 'LATIN' in unicodedata.name(ch, '')


def _is_english_segment(s: str) -> bool:
    """Only ungrouped text establishes language; explanatory qualifiers may use another script."""
    grouped = {'(': ')', '（': '）', '[': ']', '【': '】'}
    nest: list[str] = []
    seen = False
    for ch in s:
        if ch in grouped:
            nest.append(grouped[ch])
            continue
        if nest:
            if ch == nest[-1]:
                nest.pop()
            continue
        if ch.isalpha():
            if not _is_latin(ch):
                return False
            seen = True
    return seen and not nest


def _trim_slice(text: str, a: int, b: int, separators: str = '') -> tuple[int, int]:
    while a < b and (text[a].isspace() or text[a] in separators):
        a += 1
    while b > a and (text[b - 1].isspace() or text[b - 1] in separators):
        b -= 1
    return a, b


def _raw_spans(positions: list[int], a: int, b: int) -> list[list[int]]:
    ranges: list[list[int]] = []
    for i in positions[a:b]:
        if ranges and ranges[-1][1] == i:
            ranges[-1][1] = i + 1
        else:
            ranges.append([i, i + 1])
    return ranges


def adjacent_cuts(text: str) -> list[tuple[int, int]]:
    """Boundaries where an English word run that follows Chinese text meets the next Chinese name."""
    cuts = []
    for m in ADJACENT.finditer(text):
        start = m.start(1)
        run = start
        while run > 0 and not _has_han(text[run - 1]):
            run -= 1
        if run > 0 and re.search(r'[a-z]', text[run:start]):
            cuts.append(m.span(1))
    return cuts


def parse_alternatives(text: str, positions: list[int], result: dict) -> dict | None:
    """Several bilingual names joined by conjunctions, pairing each Chinese and English segment."""
    if SENTENCE.search(text):
        return None
    cuts = sorted([m.span(1) for m in ALTERNATIVE.finditer(text)] + adjacent_cuts(text))
    if not cuts:
        return None
    bounds = [0] + [x for cut in cuts for x in cut] + [len(text)]
    pairs = []
    for a, b in zip(bounds[::2], bounds[1::2]):
        boundary = next(
            (
                i for i in range(a, b)
                if _is_latin(text[i])
                and _has_han(text[a:i])
                and _is_english_segment(text[i:b])
                and sum(c.isalnum() for c in text[i:b]) >= 2
            ),
            None,
        )
        if boundary is None or any(_is_latin(ch) for ch in text[a:boundary]):
            return None
        pairs.append((_trim_slice(text, a, boundary), _trim_slice(text, boundary, b)))

    zh: list[str] = []
    en: list[str] = []
    for (za, zb), (ea, eb) in pairs:
        for language, (x, y), out in (('zh', (za, zb), zh), ('en', (ea, eb), en)):
            out.append(compact(text[x:y]))
            result['parts'].append({'language': language, 'text': out[-1], 'spans': _raw_spans(positions, x, y)})

    joiners = [re.sub(r'\s+', ' ', text[a:b]).lstrip() or '/' for a, b in cuts]
    result['annotations'].extend(
        {'role': 'alternative_separator', 'text': text[a:b], 'spans': _raw_spans(positions, a, b)}
        for a, b in cuts
    )
    result['name_zh'] = zh[0] + ''.join(j + z for j, z in zip(joiners, zh[1:]))
    result['name_en'] = ' / '.join(en)
    result.update(status='parsed', rule='bilingual_alternatives')
    return result


def parse_name(raw: object) -> dict:
    """Parse bilingual structure; all spans address the unchanged input name, not HTML."""
    raw = str(raw)
    removed: set[int] = set()
    annotations = []

    for pattern in (r'（旧版）|\(旧版\)', r'Legacy\s*$', r'(?<![A-Za-z])new!(?=\s|$)'):
        for match in re.finditer(pattern, raw, re.I):
            removed.update(range(*match.span()))
            annotations.append({'role': 'editorial_marker', 'text': match[0], 'spans': [list(match.span())]})

    level = LEVEL_LABEL.match(raw)
    if level and raw[level.end():].strip():
        removed.update(range(*level.span()))
        annotations.append({'role': 'level_label', 'text': level[0], 'value': int(level[1]), 'spans': [list(level.span())]})

    positions = [i for i in range(len(raw)) if i not in removed]
    while positions and raw[positions[0]] in ' \t\r\n｜|：:':
        positions.pop(0)
    while positions and raw[positions[-1]] in ' \t\r\n｜|：:':
        positions.pop()
    text = ''.join(raw[i] for i in positions)

    result = {
        'raw': raw,
        'offset_basis': 'raw_name_codepoints',
        'status': 'single_language',
        'rule': 'single_language',
        'name_zh': '',
        'name_en': '',
        'parts': [],
        'groups': [],
        'annotations': annotations,
    }

    def add_part(language: str, a: int, b: int) -> None:
        a, b = _trim_slice(text, a, b)
        value = compact(text[a:b])
        result['name_' + language] = value
        result['parts'].append({'language': language, 'text': value, 'spans': _raw_spans(positions, a, b)})

    def unsplit(reason: str | None = None) -> dict:
        if reason:
            result.update(status='review', rule=reason)
        if text:
            add_part('zh' if _has_han(text) else 'en', 0, len(text))
        else:
            result.update(status='review', rule='empty_name')
        return result

    pairs = {'(': ')', '（': '）', '[': ']', '【': '】', '“': '”', '「': '」', '『': '』', '"': '"'}
    stack: list[tuple[int, str]] = []
    groups: list[tuple[int, int, int]] = []
    depth: list[int] = []
    for i, ch in enumerate(text):
        depth.append(len(stack))
        if ch == '"' and stack and stack[-1][1] == '"':
            start, _ = stack.pop()
            groups.append((start, i + 1, len(stack)))
        elif ch in pairs:
            stack.append((i, ch))
        elif ch in pairs.values():
            if not stack or pairs[stack[-1][1]] != ch:
                return unsplit('unbalanced_brackets')
            start, _ = stack.pop()
            groups.append((start, i + 1, len(stack)))
    if stack:
        return unsplit('unbalanced_brackets')

    if _has_han(text) and not groups:
        alternatives = parse_alternatives(text, positions, result)
        if alternatives:
            return alternatives

    boundary = None
    wrapper = None
    if _has_han(text):
        for i, ch in enumerate(text):
            if (
                depth[i] == 0
                and (_is_latin(ch) or ch in '“「『"')
                and _has_han(text[:i])
                and _is_english_segment(text[i:])
                and sum(c.isalnum() for c in text[i:]) >= 2
            ):
                boundary = i
                while boundary > 0 and text[boundary - 1].isdigit():
                    boundary -= 1
                if not _has_han(text[:boundary]):
                    boundary = i
                qualifier = re.search(r'(?<!\S)[+−-]?\d+\s+$', text[:boundary])
                if qualifier and _has_han(text[:qualifier.start()]):
                    boundary = qualifier.start()
                break
        if boundary is None:
            for a, b, grp_level in groups:
                if grp_level == 0 and b == len(text) and text[a] in '(（' and _has_han(text[:a]) and _is_english_segment(text[a + 1:b - 1]):
                    boundary = a
                    wrapper = (a, b)
                    break

    if boundary is not None and any(b <= boundary and _is_english_segment(text[a + 1:b - 1]) for a, b, _ in groups):
        boundary = None
        wrapper = None
    if boundary is not None and adjacent_cuts(text[:boundary]):
        boundary = None
        wrapper = None

    if boundary is None:
        unsplit('mixed_name_unresolved' if _has_han(text) and any(_is_latin(ch) for ch in text) else None)
    else:
        a, b = _trim_slice(text, 0, boundary, '｜|')
        add_part('zh', a, b)
        start, end = (wrapper[0] + 1, wrapper[1] - 1) if wrapper else (boundary, len(text))
        add_part('en', start, end)
        result.update(status='parsed', rule='wrapped_translation' if wrapper else 'bilingual_suffix')

    for a, b, _ in sorted(groups):
        is_wrapper = wrapper == (a, b)
        language = None
        if is_wrapper:
            role = 'translation_wrapper'
        elif result['status'] == 'review':
            role = 'undetermined'
        else:
            role = 'quoted_name' if text[a] in '“「『"' else 'qualifier'
            language = ('en' if a >= boundary else 'zh') if boundary is not None else ('zh' if _has_han(text) else 'en')
        result['groups'].append({
            'role': role,
            'language': language,
            'text': text[a:b],
            'spans': _raw_spans(positions, a, b),
            'delimiter_spans': _raw_spans(positions, a, a + 1) + _raw_spans(positions, b - 1, b),
        })
    return result


def split_name(text: object) -> tuple[str, str]:
    """Two-field projection of the shared structural parser for existing consumers."""
    parsed = parse_name(text)
    return parsed['name_zh'], parsed['name_en']


# ---------------------------------------------------------------------------------------------------
# Domain-free Page Layout IR blocks over canonicalized HTML.

@dataclass(frozen=True)
class HeadingBlock:
    """A structural heading or opening bold run found in DOM order."""
    tag: Tag
    start: int
    end: int
    text: str
    rank: int
    method: str
    anchor: str = ''
    tail: str = ''


@dataclass(frozen=True)
class ParagraphMarks:
    """Marked inline runs inside one top-level prose paragraph."""
    start: int
    end: int
    marked_runs: tuple[tuple[Tag, int, int], ...]
    bold_runs: tuple[tuple[Tag, int, int], ...]


@dataclass(frozen=True)
class TitledList:
    """A bullet list preceded by a sibling caption element."""
    caption: str
    caption_span: tuple[int, int]
    items: tuple[tuple[str, tuple[int, int]], ...]


class Document:
    """Canonicalized HTML text with element-to-codepoint span mapping and typographical blocks."""

    def __init__(self, path: str, raw: str):
        self.path = path
        self.soup = BeautifulSoup(raw, 'html5lib')
        self.title = compact(self.soup.title.get_text(' ', strip=True)) if self.soup.title else PurePosixPath(path).stem
        self.body = self.soup.body or self.soup
        self.ranges: dict[int, tuple[int, int]] = {}
        self.text_nodes: list[tuple[int, int, NavigableString]] = []
        self.elements: list[Tag] = []
        self.anchors: dict[str, list[Tag]] = {}
        parts: list[str] = []
        size = 0

        def add(s: str) -> None:
            nonlocal size
            parts.append(s)
            size += len(s)

        def walk(node) -> None:
            if isinstance(node, (Comment, Doctype)):
                return
            if isinstance(node, NavigableString):
                start = size
                add(re.sub(r'\s+', ' ', str(node)))
                self.text_nodes.append((start, size, node))
                return
            if not isinstance(node, Tag) or node.name in IGNORED:
                return
            if node.name in BLOCKS or node.name == 'br':
                add('\n')
            start = size
            self.elements.append(node)
            for child in node.children:
                walk(child)
            self.ranges[id(node)] = (start, size)
            for attr in ('id', 'name'):
                value = node.get(attr)
                if value and node.name not in {'meta', 'input', 'select'}:
                    self.anchors.setdefault(str(value), []).append(node)
            if node.name in BLOCKS:
                add('\n')

        walk(self.body)
        self.text = ''.join(parts)

    def span(self, tag: Tag) -> tuple[int, int]:
        return self.ranges.get(id(tag), (0, 0))

    def text_of(self, tag: Tag) -> str:
        a, b = self.span(tag)
        return self.text[a:b]

    def close(self) -> None:
        self.soup.decompose()

    def dom_snapshot(self) -> dict:
        """Extract all DOM signals needed by downstream concept and link analyzers in one pass."""
        anchors = {a: [list(self.span(t)) for t in tags] for a, tags in self.anchors.items()}
        body_links = [
            {'href': a['href'], 'span': list(self.span(a)), 'link_text': compact(a.get_text(' ', strip=True))}
            for a in self.body.find_all('a', href=True)
        ]
        stylesheets: list[str] = []
        for link in self.soup.find_all('link', href=True):
            dest = resolve_link(self.path, link['href'])
            if 'stylesheet' not in link.get('rel', []) or not dest:
                continue
            if dest[0] not in stylesheets:
                stylesheets.append(dest[0])
        u_marks = []
        for tag in self.body.find_all('u'):
            a, b = self.span(tag)
            u_marks.append({
                'span': [a, b],
                'style': underline_style(tag),
                'has_parent_u': bool(tag.find_parent('u')),
                'in_explicit_parent': bool(tag.find_parent('a', href=True)),
                'has_explicit_child': bool(tag.find('a', href=True)),
                'in_example': any(EXAMPLE_CLASSES.intersection(p.get('class', [])) for p in tag.parents),
                'tag_paths': format_tag_paths(tag),
                'markup': str(tag),
            })
        concept_tables = []
        for ti, table in enumerate(self.tables()):
            if table['tag'].find_parent('table'):
                continue
            if any(EXAMPLE_CLASSES.intersection(p.get('class', [])) for p in table['tag'].parents):
                continue
            rows = table['rows']
            header = next((r for r in rows[:4] if r['cell_tags'] and all(t == 'th' for t in r['cell_tags'])), rows[0])
            headers = [compact(h) for h in header['cells']]
            cells = []
            repeated_headers = {}
            for ri, row in enumerate(rows[1:], 1):
                if row['cell_tags'] and all(t == 'th' for t in row['cell_tags']):
                    repeated_headers[ri] = row['cells']
                    continue
                for ci, cell_span in enumerate(row['cell_spans']):
                    cells.append([ri, ci, cell_span[0], cell_span[1]])
            concept_tables.append({'index': ti, 'span': list(table['span']), 'headers': headers, 'cells': cells,
                                   'repeated_headers': repeated_headers,
                                   'irregular_columns': any(any(n!='1' for n in r['rowspan']) or
                                       len(r['cells'])>1 and any(n!='1' for n in r['colspan']) for r in rows)})
        non_prose_spans = [
            list(self.span(t)) for t in self.elements
            if t.name in {'table', 'blockquote'} or EXAMPLE_CLASSES.intersection(t.get('class', []))
        ]
        return {
            'anchors': anchors,
            'body_links': body_links,
            'stylesheets': stylesheets,
            'u_marks': u_marks,
            'concept_tables': concept_tables,
            'non_prose_spans': non_prose_spans,
        }

    def tables(self) -> list[dict]:
        out = []
        for table in self.body.find_all('table'):
            a, b = self.span(table)
            caption = table.find_previous(['p', *HEADINGS])
            caption_text = ''
            if caption:
                ca, cb = self.span(caption)
                caption_text = compact(self.text[ca:min(a, cb)])
            rows = []
            for tr in table.find_all('tr'):
                if tr.find_parent('table') is not table:
                    continue
                cells = tr.find_all(['th', 'td'], recursive=False)
                if not cells:
                    continue
                rows.append({
                    'cells': [compact(c.get_text(' ', strip=True)) for c in cells],
                    'cell_spans': [self.span(c) for c in cells],
                    'rowspan': [c.get('rowspan', '1') for c in cells],
                    'colspan': [c.get('colspan', '1') for c in cells],
                    'cell_tags': [c.name for c in cells],
                    'span': self.span(tr),
                    'tag': tr,
                })
            if rows:
                out.append({'span': (a, b), 'rows': rows, 'tag': table, 'preceding_text': caption_text})
        return out

    def tagged_rows(self, attributes: tuple[str, ...]) -> list[dict]:
        """Extract <tr> rows carrying one of the given marker attributes (e.g. WinCHM index tables)."""
        out = []
        for n, tr in enumerate(self.body.find_all('tr')):
            attr = next((a for a in attributes if tr.has_attr(a)), None)
            if not attr:
                continue
            cells = tr.find_all(['td', 'th'], recursive=False)
            vals = [compact(c.get_text(' ', strip=True)) for c in cells]
            links = list(cells[0].find_all('a', href=True)) if cells else []
            table = tr.find_parent('table')
            headers = []
            if table:
                first = table.find('tr')
                if first:
                    headers = [compact(c.get_text(' ', strip=True)) for c in first.find_all(['th', 'td'], recursive=False)]
            href = links[0]['href'] if links else ''
            target = resolve_link(self.path, href) if href else None
            out.append({
                'id': digest(self.path, 'row', n),
                'path': self.path,
                'row': n,
                'span': list(self.span(tr)),
                'attr': attr,
                'name': compact(tr.get(attr, '')),
                'headers': headers,
                'cells': vals,
                'tags': compact(tr.get('tags', '')),
                'href': href,
                'target_path': target[0] if target else '',
                'target_anchor': target[1] if target else '',
                'legacy': bool(tr.select('.legacy')) or bool(LEGACY.search(vals[0] if vals else '')),
            })
        return out

    def _earliest_table_start(self, tag: Tag, a: int, b: int) -> int | None:
        return min(
            (self.span(tbl)[0] for tbl in tag.find_all('table') if id(tbl) in self.ranges and a <= self.span(tbl)[0] < b),
            default=None,
        )

    def _has_bold_before(self, tag: Tag, limit: int) -> bool:
        return any(
            id(bt) in self.ranges and self.span(bt)[0] < limit
            for bt in tag.find_all(['b', 'strong'])
        )

    def _covered_by_non_maroon(self, tag: Tag, a: int, b: int) -> bool:
        return any(
            id(d) in self.ranges
            and _is_non_maroon_color(d)
            and self.span(d)[0] <= a
            and self.span(d)[1] >= b
            for d in tag.find_all(['font', 'span'])
        )

    def _clip_colored_heading_end(self, tag: Tag, a: int, b: int) -> int:
        cuts: list[int] = []
        tbl_cut = self._earliest_table_start(tag, a, b)
        if tbl_cut is not None:
            cuts.append(tbl_cut)
        for d in tag.find_all(['font', 'span']):
            if id(d) not in self.ranges or not _is_non_maroon_color(d):
                continue
            da, db = self.span(d)
            if not (a <= da < b) or not compact(self.text[a:da]):
                continue
            if '\n' not in self.text[a:da] and not self.text[da:db].lstrip(' \t').startswith('\n'):
                continue
            inside = compact(self.text[da:db])
            if not inside or PURE_ASCII_TITLE_RE.fullmatch(inside):
                continue
            cuts.append(da)
        return _trim_span_end(self.text, a, min(cuts)) if cuts else b

    def element_headings(self) -> list[HeadingBlock]:
        """Yield structural headings and paragraph-opening bold leads in DOM order."""
        blocks: list[HeadingBlock] = []
        for tag in self.elements:
            if id(tag) not in self.ranges:
                continue
            a, b = self.span(tag)
            if tag.name in HEADINGS:
                cut = self._earliest_table_start(tag, a, b)
                if cut is not None:
                    b = _trim_span_end(self.text, a, cut)
                txt = compact(self.text[a:b])
                if txt:
                    blocks.append(HeadingBlock(tag, a, b, txt, int(tag.name[1]), 'heading', anchor=tag.get('id', '')))
                continue
            if tag.name in {'b', 'strong'} and not tag.find_parent(list(HEADINGS)):
                block = tag.find_parent('p')
                if block and not block.find_parent(['li', 'table']):
                    ba, bb = self.span(block)
                    if not compact(self.text[ba:a]):
                        blocks.append(HeadingBlock(tag, a, b, compact(self.text[a:b]), 4, 'opening_bold', tail=compact(self.text[b:bb])))
            if tag.name == 'p':
                style = tag.get('style', '').lower()
                if 'border-bottom' in style:
                    cut = self._earliest_table_start(tag, a, b)
                    if cut is not None:
                        b = _trim_span_end(self.text, a, cut)
                    txt = compact(self.text[a:b])
                    if txt and len(txt) < 150 and self._has_bold_before(tag, b):
                        blocks.append(HeadingBlock(tag, a, b, txt, 4, 'word_heading'))
                        continue
            if _is_maroon(tag) and tag.name in {'font', 'span'} and not tag.find_parent(list(HEADINGS)):
                if self._covered_by_non_maroon(tag, a, b) and any(
                    id(d) in self.ranges
                    and _is_maroon(d)
                    and not compact(self.text[a:self.span(d)[0]])
                    and self.span(d)[1] < b
                    and compact(self.text[self.span(d)[1]:b])
                    for d in tag.find_all(['font', 'span'])
                ):
                    continue
                orig_b = b
                raw_len = len(compact(self.text[a:orig_b]))
                b = self._clip_colored_heading_end(tag, a, b)
                if raw_len >= 150 and (
                    b >= orig_b
                    or tag.find(['p', 'div'])
                    or self._covered_by_non_maroon(tag, a, b)
                ):
                    continue
                txt = compact(self.text[a:b])
                if not 1 < len(txt) < 150:
                    continue
                block = tag.find_parent(['p', 'li', 'div', 'td', 'body'])
                if block is None:
                    continue
                prefix = self.text[self.span(block)[0]:a]
                if compact(prefix.split('\n')[-1]):
                    continue
                if block.name == 'p' and 'border-bottom' in block.get('style', '').lower():
                    continue
                if not (self._has_bold_before(tag, b) or tag.find_parent(['b', 'strong']) or re.search('[A-Za-z]{3}', txt)):
                    continue
                blocks.append(HeadingBlock(tag, a, b, txt, 4, 'colored_heading'))
        return blocks

    def paragraph_marks(self) -> list[ParagraphMarks]:
        """Top-level prose paragraphs and their inner marked/bold tag spans."""
        result: list[ParagraphMarks] = []
        for block in self.body.find_all('p'):
            if block.find_parent(['table', 'li']):
                continue
            ba, bb = self.span(block)
            marked = tuple((t, *self.span(t)) for t in block.find_all(['u', 'b', 'strong']))
            bold = tuple((t, *self.span(t)) for t in block.find_all(['b', 'strong']))
            result.append(ParagraphMarks(ba, bb, marked, bold))
        return result

    def line_leads(self) -> list[tuple[int, int, str]]:
        """Bold runs that open a line and end at 。, ：, : or .: (start, end, name) in text order."""
        runs: list[list[int]] = []
        for tag in self.body.find_all(['b', 'strong']):
            if tag.find_parent(['b', 'strong', 'table', 'a'] + list(HEADINGS)):
                continue
            a, b = self.span(tag)
            if a >= b:
                continue
            if runs and runs[-1][1] <= a and not compact(self.text[runs[-1][1]:a]):
                runs[-1][1] = max(runs[-1][1], b)
                continue
            line = self.text.rfind('\n', 0, a) + 1
            if compact(self.text[line:a]):
                continue
            runs.append([a, b])
        leads: list[tuple[int, int, str]] = []
        for a, b in runs:
            raw = compact(self.text[a:b])
            after = self.text[b:b + 3].lstrip(' ')
            name = raw.rstrip('。:：.')
            if not name or (raw == name and after[:1] not in {'。', '：', ':', '.'}):
                continue
            leads.append((a, b, name))
        return leads

    def standalone_bilingual_lines(self) -> set[int]:
        """Start offsets of standalone unpunctuated bilingual lines (e.g. sidebar banners)."""
        bounds: set[int] = set()
        for m in re.finditer(r'(?m)^[ \t]*(\S[^\n]{0,80}?)[ \t]*$', self.text):
            title = compact(m[1])
            zh, en = split_name(title)
            if zh and en and not re.search(r'[。！？；，、]', title):
                bounds.add(m.start(1))
        return bounds

    def titled_tables(self, exclude_title=None):
        """Pair a caption or standalone preceding marked line with its adjacent data table."""
        title_tags = HEADINGS | {'u', 'b', 'strong'}

        def short_title(tag: Tag) -> bool:
            name = compact(self.text_of(tag))
            if not (1 < len(name) <= 120 and not re.search(r'[。；！？!?]', name)):
                return False
            if DICE_OR_RANGE_RE.fullmatch(name):
                return False
            return not (exclude_title and exclude_title(name))

        for table in self.body.find_all('table'):
            if table.find_parent('table') or table.find_parent(class_=['stat-block', 'statblock']):
                continue
            rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
            if len(rows) < 2 or not any(len(r.find_all(['td', 'th'], recursive=False)) >= 2 for r in rows):
                continue
            caption = table.find('caption', recursive=False)
            if caption and short_title(caption) and self.span(caption)[1] <= self.span(rows[0])[0]:
                yield caption, self.span(table)
                continue
            previous = table.previous_sibling
            while previous is not None and (
                isinstance(previous, Comment)
                or (isinstance(previous, NavigableString) and not str(previous).strip())
                or (isinstance(previous, Tag) and previous.name == 'br')
            ):
                previous = previous.previous_sibling
            if not isinstance(previous, Tag) or previous.name == 'table':
                continue
            ta, tb = self.span(table)
            matches = []
            for tag in [previous] + list(previous.find_all(list(title_tags))):
                if tag.name not in title_tags or tag.find_parent(['td', 'th', 'table']) or not short_title(tag):
                    continue
                a, b = self.span(tag)
                if b > ta or compact(self.text[b:ta]):
                    continue
                line_start = self.text.rfind('\n', 0, a) + 1
                if compact(self.text[line_start:a]):
                    continue
                matches.append(tag)
            spans = {self.span(t) for t in matches}
            if len(spans) == 1:
                yield matches[0], (ta, tb)

    def boxed_headings(self, selector: str = '.stat-block, .statblock') -> list[tuple[Tag, str, int]]:
        """Boxed card containers carrying an internal <h1..h6> title and explicit box end."""
        out = []
        for box in self.body.select(selector):
            title = box.find(list(HEADINGS))
            if title and id(title) in self.ranges:
                out.append((title, title.get('id', ''), self.span(box)[1]))
        return out

    def preceding_title(self, pos: int) -> tuple[int, int, str] | None:
        """Standalone title line (or Chinese + English line pair) immediately preceding `pos`."""
        prefix = self.text[:pos].rstrip()
        a = prefix.rfind('\n') + 1
        title = compact(prefix[a:])
        if title and not _has_han(title):
            before = prefix[:a].rstrip()
            b = before.rfind('\n') + 1
            chinese = compact(before[b:])
            if chinese and len(chinese) < 100 and _has_han(chinese) and not re.search(r'[A-Za-z。；！？]', chinese):
                a = b
                title = compact(prefix[a:])
        if not title or len(title) > 150 or re.search(r'[。；！？]', title):
            return None
        return a, len(prefix), title

    def anchor_target(self, anchor: str, candidate_starts_near) -> tuple[Tag, int | None] | None:
        """Resolve a unique DOM anchor to its target tag (or enclosing/nearby heading) and optional stat-block end."""
        tags = self.anchors.get(anchor, []) if anchor else []
        if len(tags) != 1:
            return None
        tag = tags[0]
        if not compact(self.text_of(tag)):
            parent = tag.find_parent(['p'] + list(HEADINGS))
            if parent and len(compact(self.text_of(parent))) < 200:
                tag = parent
            else:
                near_tag = candidate_starts_near(self.span(tag)[0])
                if near_tag is not None:
                    tag = near_tag
        container = tag.find_parent(class_='stat-block')
        return tag, self.span(container)[1] if container else None

    def titled_lists(self) -> list[TitledList]:
        """Bullet lists paired with their immediate preceding sibling caption."""
        out: list[TitledList] = []
        for ul in self.body.find_all('ul'):
            caption = ul.find_previous_sibling()
            if not caption:
                continue
            items = tuple(
                (compact(li.get_text(' ', strip=True)), self.span(li))
                for li in ul.find_all('li', recursive=False)
            )
            out.append(TitledList(compact(caption.get_text(' ', strip=True)), self.span(caption), items))
        return out

    def _usable_prose_tag(self, tag: Tag) -> bool:
        return not (
            tag.find_parent(['table', 'a', 'blockquote'] + list(HEADINGS))
            or any(EXAMPLE_CLASSES.intersection(p.get('class', [])) for p in tag.parents)
        )

    def underlined_gloss_series(self, start: int, end: int):
        """Declared series of underlined Chinese terms, each followed by a parenthesized gloss."""
        for block in self.body.find_all('p'):
            lo, hi = self.span(block)
            if lo < start or hi > end or not self._usable_prose_tag(block):
                continue
            terms = {}
            for tag in block.find_all('u', recursive=False):
                a, b = self.span(tag)
                name = self.text[a:b]
                if re.fullmatch(r'[\u3400-\u9fff]{2,24}', name):
                    terms[a] = b
            for intro in re.finditer(r'([^。！？；\n：:]+)[：:]\s*', self.text[lo:hi]):
                lead = intro[1]
                if not re.search(r'(?:[两二三四五六七八九十\d]+种|分为|包括|下列|以下|分别)', lead):
                    continue
                if re.search(r'例如|比如|举例|譬如', lead):
                    continue
                items = []
                a = lo + intro.end()
                while a in terms:
                    b = terms[a]
                    tail = self.text[b:hi]
                    opening = re.match(r'\s*([（(])', tail)
                    if not opening:
                        break
                    stack = [opening[1]]
                    pos = b + opening.end()
                    for pos in range(b + opening.end(), min(hi, b + 180)):
                        ch = self.text[pos]
                        if ch in '（(':
                            stack.append(ch)
                        elif ch in '）)':
                            if ch != ('）' if stack[-1] == '（' else ')'):
                                break
                            stack.pop()
                            if not stack:
                                break
                        elif ch == '\n':
                            break
                    if stack:
                        break
                    gloss = compact(self.text[b + opening.end():pos])
                    if len(gloss) < 4 or not _has_han(gloss):
                        break
                    items.append((a, b, pos + 1, self.text[a:b]))
                    separator = re.match(r'\s*[、，,]\s*', self.text[pos + 1:hi])
                    if not separator:
                        after = self.text[pos + 1:hi].lstrip()
                        if len(items) >= 2 and len({key(t[3]) for t in items}) == len(items) and (not after or after[0] in '。；;.!！？'):
                            yield items
                        break
                    a = pos + 1 + separator.end()

    def parallel_bold_lines(self, start: int, end: int):
        """Groups of sibling bold bilingual runs sharing an inline-heading or clause frame."""
        lines: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for tag in self.body.find_all(['b', 'strong']):
            a, b = self.span(tag)
            if not start <= a < b <= end or tag.find_parent(['b', 'strong']) or not self._usable_prose_tag(tag):
                continue
            line = max(start, self.text.rfind('\n', start, a) + 1)
            stop = self.text.find('\n', a, end)
            if stop < 0:
                stop = end
            if b <= stop:
                lines[(line, stop)].append((a, b))

        siblings: dict[object, list[tuple[int, int, int, str]]] = defaultdict(list)
        for (line, stop), parts in lines.items():
            a, b = parts[0]
            for x, y in parts[1:]:
                if compact(self.text[b:x]):
                    break
                b = y
            prefix = compact(self.text[line:a])
            raw = compact(self.text[a:b])
            name = raw.rstrip('。:：')
            zh, en = split_name(name)
            if not (zh and en and len(name) <= 80 and not re.search(r'[。！？；：:]', name)):
                continue
            tail = compact(self.text[b:stop])
            if len(tail) < 8 or not re.search(r'[。！？]', tail):
                continue
            if not prefix and raw != name:
                signature: object = 'inline_heading'
            elif re.fullmatch(r'(?:当(?:你|目标|生物)?)?(?:处于|具有|获得|陷入|使用|执行|受到)', prefix):
                continuation = re.match(r'(?:下的|时[，,]|期间|中的|中[，,])', tail)
                if not continuation:
                    continue
                signature = (prefix, continuation[0])
            else:
                continue
            siblings[signature].append((a, b, b + len(self.text[b:stop].rstrip()), name))

        for group in siblings.values():
            if len(group) >= 2 and len({key(t[3]) for t in group}) == len(group):
                yield group

    def legacy_marker_spans(self) -> list[tuple[int, int]]:
        return [
            self.span(t) for t in self.elements
            if t.name in {'sup', 'em', 'i'} and compact(self.text_of(t)).casefold() == 'legacy'
        ]

    def has_local_legacy(
        self,
        name: str,
        start: int,
        title_end: int,
        marker_limit: int,
        legacy_markers: list[tuple[int, int]],
        tag: Tag | None = None,
    ) -> bool:
        if LOCAL_LEGACY_RE.search(name):
            return True
        if any(start <= a < marker_limit and b <= marker_limit for a, b in legacy_markers):
            return True
        if tag is not None:
            block = tag.find_parent(['p'] + list(HEADINGS)) or tag
            a, b = self.span(block)
            if a <= start and b >= title_end:
                for marker in block.find_all(['sup', 'em', 'i']):
                    ma, mb = self.span(marker)
                    if start <= ma <= title_end + 40 and compact(self.text[ma:mb]).casefold() == 'legacy':
                        return True
        return False
