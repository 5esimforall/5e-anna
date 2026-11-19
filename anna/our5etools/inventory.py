"""Snapshot inventory, file classification, source parsing, and brew reconstruction for our5etools."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any


TOOL_DATA_NAMES: frozenset[str] = frozenset({
    'changelog.json',
    'converter.json',
    'encounterbuilder.json',
    'makebrew-creature.json',
    'makecards.json',
    'msbcr.json',
    'renderdemo.json',
})

INDEX_DATA_NAMES: frozenset[str] = frozenset({
    'index.json',
    'fluff-index.json',
    'sources.json',
})

FONT_EXTS: frozenset[str] = frozenset({'.otf', '.ttf', '.woff', '.woff2'})
IMAGE_EXTS: frozenset[str] = frozenset({'.png', '.webp', '.svg', '.jpg', '.jpeg', '.gif', '.ico'})
AUDIO_EXTS: frozenset[str] = frozenset({'.opus', '.mp3', '.ogg', '.wav'})

DISPOSITIONS: frozenset[str] = frozenset({
    'entity_data',
    'fluff_data',
    'book_data',
    'adventure_data',
    'brew_shard',
    'brew_recipe',
    'site_brew',
    'site_metadata',
    'metadata_alias',
    'derived_index',
    'tool_excluded',
    'local_media',
    'i18n',
    'web_runtime',
    'repo_helper',
})

SITE_METADATA_PATHS: frozenset[str] = frozenset({
    '__ours/ratings.json',
    '__ours/assets.json',
    '__ours/fonts/OFL.txt',
    '__ours/fonts/common-3500.txt',
    '__ours/fonts/coverage.json',
    '__brewrepo/files.json',
    '__prerelease/empty.json',
    'prerelease/index.json',
    'homebrew/index.json',
    'homebrew/pages.json',
    'homebrew/sources.json',
    'data/bestiary/template.json',
    'manifest.webmanifest',
    'browserconfig.xml',
    'open-search.xml',
    'robots.txt',
    'llms.txt',
    'llms-full.txt',
})

GENERATED_LOOKUP_PATHS: tuple[str, ...] = (
    'data/generated/gendata-spell-source-lookup.json',
    'data/generated/gendata-maps.json',
    'data/generated/gendata-tag-redirects.json',
    'data/generated/gendata-nav-adventure-book-index.json',
    'data/spells/sources.json',
    'data/bestiary/template.json',
    'data/bestiary/legendarygroups.json',
)

SKIP_TOP_DIRS: frozenset[str] = frozenset({'.git', 'resource-export'})

XPHB_DEFAULT_DATE = '2024-09-17'

_MIME_OVERRIDES: dict[str, str] = {
    '.opus': 'audio/opus',
    '.webp': 'image/webp',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.otf': 'font/otf',
    '.svg': 'image/svg+xml',
}


@dataclass
class Inventory:
    """Complete read-only snapshot of a local our5etools repository."""

    source_root: Path
    snapshot_info: dict
    file_records: list[dict]
    official_sources: dict[str, dict]
    official_source_casefold: dict[str, str]
    official_docs: list[dict]
    generated_lookups: dict
    packages: list[dict]
    package_docs: dict[str, dict]
    ratings: dict
    media_files: dict[str, dict]
    checks: dict

    def resolve_media(self, relpath: str) -> dict | None:
        """Resolve a local media path (repo-relative or audio-subpath) to its metadata record."""
        if not relpath:
            return None
        cleaned = relpath.lstrip('/').replace('\\', '/')
        if cleaned in self.media_files:
            return self.media_files[cleaned]
        audio_candidate = f'audio/{cleaned}'
        if audio_candidate in self.media_files:
            return self.media_files[audio_candidate]
        return None

    def has_media(self, relpath: str) -> bool:
        """Return True when relpath exists as an indexed local media file."""
        return self.resolve_media(relpath) is not None


def _checked_relative(name: str) -> PurePosixPath:
    """Validate a POSIX relative path and reject traversal or absolute paths."""
    p = PurePosixPath(name)
    if not p.parts or p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError(f'Not a safe relative path: {name!r}')
    return p


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json_bytes(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _guess_mime(ext: str) -> str:
    if ext in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[ext]
    mime, _ = mimetypes.guess_type(f'file{ext}')
    return mime or 'application/octet-stream'


def _strip_js_comments(source: str) -> str:
    """Strip JS // and /* */ comments while preserving string and template literals."""
    out: list[str] = []
    i = 0
    n = len(source)
    quote: str | None = None
    while i < n:
        ch = source[i]
        if quote is not None:
            out.append(ch)
            if ch == '\\' and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'", '`'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == '/' and i + 1 < n:
            nxt = source[i + 1]
            if nxt == '/':
                i += 2
                while i < n and source[i] != '\n':
                    i += 1
                continue
            if nxt == '*':
                i += 2
                while i + 1 < n and not (source[i] == '*' and source[i + 1] == '/'):
                    if source[i] == '\n':
                        out.append('\n')
                    i += 1
                i += 2
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _decode_js_string(expr: str, constants: dict[str, str]) -> str | None:
    """Evaluate a simple JS string literal, template literal, or Parser.<CONST> expression."""
    s = expr.strip()
    if not s:
        return None
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        inner = s[1:-1].replace('\\"', '"').replace('"', '\\"').replace("\\'", "'")
        try:
            return json.loads(f'"{inner}"')
        except json.JSONDecodeError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == '`' and s[-1] == '`':
        inner = s[1:-1]
        unresolved = False

        def _subst(match: re.Match[str]) -> str:
            nonlocal unresolved
            name = match.group(1)
            if name in constants:
                return constants[name]
            unresolved = True
            return match.group(0)

        rendered = re.sub(r'\$\{\s*Parser\.([A-Za-z0-9_]+)\s*\}', _subst, inner)
        if unresolved:
            return None
        return rendered
    const_match = re.fullmatch(r'Parser\.([A-Za-z0-9_]+)', s)
    if const_match:
        return constants.get(const_match.group(1))
    return None


def _resolve_js_key(raw_key: str, constants: dict[str, str]) -> str | None:
    """Resolve a map key expression such as Parser.SRC_PHB or 'PHB'."""
    val = _decode_js_string(raw_key, constants)
    if val is not None:
        return val
    return None


def _extract_js_set(clean_js: str, attr_name: str, constants: dict[str, str]) -> set[str]:
    """Extract elements from Parser.<attr_name> = new Set([ ... ]);."""
    pattern = re.compile(
        rf'Parser\.{re.escape(attr_name)}\s*=\s*new\s+Set\s*\(\s*\[(.*?)\]\s*\)',
        re.DOTALL,
    )
    match = pattern.search(clean_js)
    if not match:
        return set()
    body = match.group(1)
    result: set[str] = set()
    for token in body.split(','):
        val = _resolve_js_key(token, constants)
        if val:
            result.add(val)
    return result


def _extract_available_docs_map(
    clean_js: str,
    attr_name: str,
    constants: dict[str, str],
) -> dict[str, str]:
    """Extract Parser.SOURCES_AVAILABLE_DOCS_BOOK / _ADVENTURE mappings."""
    anchor = f'Parser.{attr_name} = {{}};'
    start = clean_js.find(anchor)
    if start == -1:
        # Tolerate whitespace variations.
        m = re.search(rf'Parser\.{re.escape(attr_name)}\s*=\s*\{{\s*\}}\s*;', clean_js)
        if not m:
            return {}
        start = m.end()
    else:
        start += len(anchor)

    # Scan consecutive `[...].forEach(...)` blocks immediately following the declaration.
    tail = clean_js[start:]
    result: dict[str, str] = {}
    pos = 0
    while True:
        while pos < len(tail) and tail[pos].isspace():
            pos += 1
        if pos >= len(tail) or tail[pos] != '[':
            break
        # Find matching ']'
        depth = 0
        end_bracket = -1
        for idx in range(pos, len(tail)):
            if tail[idx] == '[':
                depth += 1
            elif tail[idx] == ']':
                depth -= 1
                if depth == 0:
                    end_bracket = idx
                    break
        if end_bracket == -1:
            break
        array_body = tail[pos + 1 : end_bracket]
        after = tail[end_bracket + 1 :]
        foreach_match = re.match(r'\s*\.forEach\s*\(.*?\}\s*\)\s*;', after, re.DOTALL)
        if not foreach_match:
            break
        # Check if object literals `{src: ..., id: ...}` or bare items.
        obj_matches = list(re.finditer(
            r'\{\s*src\s*:\s*([^,}]+?)\s*,\s*id\s*:\s*([^}]+?)\s*\}',
            array_body,
        ))
        if obj_matches:
            for om in obj_matches:
                src_val = _resolve_js_key(om.group(1), constants)
                id_val = _decode_js_string(om.group(2), constants)
                if src_val and id_val:
                    result[src_val] = id_val
        else:
            for item in array_body.split(','):
                src_val = _resolve_js_key(item, constants)
                if src_val:
                    result[src_val] = src_val
        pos = end_bracket + 1 + foreach_match.end()
    return result


def parse_official_sources(parser_js: str) -> tuple[dict[str, dict], dict[str, str], list[str]]:
    """Parse official source definitions and category sets from 5etools parser.js."""
    clean_js = _strip_js_comments(parser_js)
    warnings: list[str] = []

    # 1. Extract Parser.<CONST> = <string / template / const>; assignments in declaration order.
    constants: dict[str, str] = {}
    const_re = re.compile(
        r'^\s*Parser\.([A-Za-z0-9_]+)\s*=\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`|Parser\.[A-Za-z0-9_]+)\s*;',
        re.MULTILINE,
    )
    for m in const_re.finditer(clean_js):
        name, rhs = m.group(1), m.group(2)
        val = _decode_js_string(rhs, constants)
        if val is not None:
            constants[name] = val

    # 2. Extract SOURCE_JSON_TO_FULL / ABV / DATE maps.
    maps: dict[str, dict[str, str]] = {
        'SOURCE_JSON_TO_FULL': {},
        'SOURCE_JSON_TO_ABV': {},
        'SOURCE_JSON_TO_DATE': {},
    }
    map_re = re.compile(
        r'^\s*Parser\.(SOURCE_JSON_TO_(?:FULL|ABV|DATE))\[([^\]]+)\]\s*=\s*'
        r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`|[^;\n]+?)\s*;',
        re.MULTILINE,
    )
    alias_re = re.compile(r'^Parser\.(SOURCE_JSON_TO_(?:FULL|ABV|DATE))\[([^\]]+)\]$')

    for m in map_re.finditer(clean_js):
        map_name, raw_key, raw_val = m.group(1), m.group(2), m.group(3)
        key = _resolve_js_key(raw_key, constants)
        if not key:
            warnings.append(f'Unresolved key in {map_name}: {raw_key.strip()}')
            continue
        val = _decode_js_string(raw_val, constants)
        if val is None:
            alias = alias_re.fullmatch(raw_val.strip())
            if alias:
                ref_map, ref_raw_key = alias.group(1), alias.group(2)
                ref_key = _resolve_js_key(ref_raw_key, constants)
                if ref_key and ref_key in maps[ref_map]:
                    val = maps[ref_map][ref_key]
        if val is None:
            warnings.append(f'Unresolved value in {map_name}[{key}]: {raw_val.strip()}')
            continue
        maps[map_name][key] = val

    # 3. Extract category Sets and available-docs maps.
    partnered = _extract_js_set(clean_js, 'SOURCES_PARTNERED_WOTC', constants)
    non_standard = _extract_js_set(clean_js, 'SOURCES_NON_STANDARD_WOTC', constants)
    legacy = _extract_js_set(clean_js, 'SOURCES_LEGACY_WOTC', constants)
    adventures = _extract_js_set(clean_js, 'SOURCES_ADVENTURES', constants)
    docs_book = _extract_available_docs_map(clean_js, 'SOURCES_AVAILABLE_DOCS_BOOK', constants)
    docs_adventure = _extract_available_docs_map(clean_js, 'SOURCES_AVAILABLE_DOCS_ADVENTURE', constants)

    full_map = maps['SOURCE_JSON_TO_FULL']
    abv_map = maps['SOURCE_JSON_TO_ABV']
    date_map = maps['SOURCE_JSON_TO_DATE']
    xphb_date = date_map.get('XPHB', XPHB_DEFAULT_DATE)

    all_codes: list[str] = []
    seen_codes: set[str] = set()
    for container in (full_map, abv_map, date_map, partnered, non_standard, legacy, docs_book, docs_adventure):
        for code in container:
            if code not in seen_codes:
                seen_codes.add(code)
                all_codes.append(code)

    official_sources: dict[str, dict] = {}
    official_source_casefold: dict[str, str] = {}

    for code in all_codes:
        date = date_map.get(code)
        if date and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            is_classic: bool | None = date < xphb_date
        else:
            is_classic = None
        abv = abv_map.get(code, code)
        full = full_map.get(code, code)
        official_sources[code] = {
            'code': code,
            'json': code,
            'full': full,
            'abv': abv,
            'abbreviation': abv,
            'date': date,
            'is_classic': is_classic,
            'edition': 'classic' if is_classic is True else ('one' if is_classic is False else None),
            'is_partnered': code in partnered,
            'is_non_standard': code in non_standard,
            'is_legacy': code in legacy,
            'is_adventure': code in adventures,
            'book_doc_id': docs_book.get(code),
            'adventure_doc_id': docs_adventure.get(code),
            'has_book_doc': code in docs_book,
            'has_adventure_doc': code in docs_adventure,
        }
        official_source_casefold[code.casefold()] = code

    return official_sources, official_source_casefold, warnings


def _classify_file(
    rel: PurePosixPath,
    site_brew_paths: set[str],
) -> tuple[str, str, str, str]:
    """Return (category, role, disposition, reason) for a repo-relative file path."""
    posix = rel.as_posix()
    parts = rel.parts
    top = parts[0]
    name = rel.name
    ext = rel.suffix.lower()

    # Repo / maintenance helpers
    if top == 'tools':
        return ('repo_helper', 'export_tool', 'repo_helper', 'Repository helper script or bytecode under tools/')
    if name in {'.gitignore', '.nojekyll'}:
        return ('repo_helper', 'repo_config', 'repo_helper', 'Repository git/hosting marker file')
    if top == 'homebrew' and ext == '.py':
        return ('repo_helper', 'homebrew_script', 'repo_helper', 'Homebrew maintenance script')

    # Site-bundled homebrew imports declared in homebrew/index.json['toImport']
    if posix in site_brew_paths:
        return ('site_brew', 'site_homebrew_package', 'site_brew', 'Standalone site homebrew declared in homebrew/index.json')

    # Homebrew shards, recipes, and derived caches
    if parts[:2] == ('homebrew', '_data'):
        return ('homebrew', 'brew_shard', 'brew_shard', 'Reconstructible homebrew content shard')
    if parts[:2] == ('homebrew', '_recipes'):
        return ('homebrew', 'brew_recipe', 'brew_recipe', 'Homebrew package assembly recipe')
    if parts[:2] in {('homebrew', '_bundle'), ('homebrew', '_indexes')}:
        return ('derived_index', 'homebrew_cache', 'derived_index', 'Derived homebrew bundle or search index')

    # Derived search / generated indexes
    if top == 'search' and ext == '.json':
        return ('derived_index', 'search_index', 'derived_index', 'Prebuilt client search index')
    if parts[:2] in {('__brewrepo', '_generated'), ('__prerelease', '_generated')}:
        return ('derived_index', 'repo_generated_index', 'derived_index', 'Generated repository lookup index')

    # API metadata and extensionless aliases
    if top == '__api':
        if ext == '.json':
            return ('site_metadata', 'api_metadata', 'site_metadata', 'Static site API metadata JSON')
        return ('site_metadata', 'api_alias', 'metadata_alias', 'Extensionless duplicate of __api/*.json')

    # Explicit site metadata files (including data/bestiary/template.json, __ours/assets.json, etc.)
    if posix in SITE_METADATA_PATHS:
        if posix == 'data/bestiary/template.json':
            return ('official_data', 'copy_template', 'site_metadata', 'Bestiary _copy template definitions loaded via generated_lookups')
        if parts[:2] == ('__ours', 'fonts'):
            return ('site_metadata', 'font_metadata', 'site_metadata', 'Local font license or glyph coverage metadata')
        return ('site_metadata', 'site_metadata', 'site_metadata', 'Site catalog, runtime bundle, or configuration metadata')

    # Official data/ tree
    if top == 'data':
        if 'generated' in parts or name in INDEX_DATA_NAMES:
            return ('derived_index', 'official_derived_index', 'derived_index', 'Generated or directory index JSON under data/')
        if name.startswith('foundry') or name in TOOL_DATA_NAMES:
            return ('tool_data', 'tool_or_vtt_data', 'tool_excluded', 'Foundry VTT or client tool-only JSON')
        if name.startswith('fluff-'):
            return ('official_data', 'official_fluff', 'fluff_data', 'Official descriptive fluff and image metadata')
        if parts[:2] == ('data', 'book'):
            return ('official_data', 'official_book_body', 'book_data', 'Official book chapter body JSON')
        if parts[:2] == ('data', 'adventure'):
            return ('official_data', 'official_adventure_body', 'adventure_data', 'Official adventure chapter body JSON')
        if name in {'books.json', 'adventures.json'}:
            role = 'official_book_catalog' if name == 'books.json' else 'official_adventure_catalog'
            return ('official_data', role, 'entity_data', 'Official book/adventure catalog entries')
        if ext == '.json':
            return ('official_data', 'official_entity', 'entity_data', 'Official rules entity data JSON')
        return ('official_data', 'official_other', 'site_metadata', 'Non-JSON file under data/')

    # Local media files (audio, fonts, images)
    if ext in AUDIO_EXTS:
        return ('local_media', 'audio', 'local_media', 'Local audio asset')
    if ext in FONT_EXTS:
        return ('local_media', 'font', 'local_media', 'Local font asset')
    if ext in IMAGE_EXTS:
        return ('local_media', 'image', 'local_media', 'Local image asset')

    # UI localization
    if top == 'languages':
        return ('i18n', 'ui_translation', 'i18n', 'Client UI localization resource')

    # Web frontend runtime
    if ext in {'.html', '.htm', '.js', '.css'} or top in {'js', 'css', 'lib', 'shell', '__ours'}:
        return ('web_runtime', 'frontend_asset', 'web_runtime', 'Static web frontend runtime asset')

    return ('web_runtime', 'site_misc', 'web_runtime', 'Other static site file')


def _git_commit(source_root: Path) -> str:
    try:
        proc = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=source_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        commit = proc.stdout.strip()
        if proc.returncode == 0 and commit:
            return commit
    except (OSError, subprocess.SubprocessError):
        pass
    return 'unversioned'


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _raw_record_natural_key(raw_kind: str, item: dict) -> tuple[Any, ...]:
    """Compute a natural identity tuple for duplicate detection inside one package & kind."""
    def _norm(val: Any) -> Any:
        if isinstance(val, (dict, list)):
            return _canonical_json(val)
        return val

    name = item.get('name')
    if not name and isinstance(item.get('_copy'), dict):
        name = item['_copy'].get('name')
    eng_name = item.get('ENG_name')
    if not eng_name and isinstance(item.get('_copy'), dict):
        eng_name = item['_copy'].get('ENG_name')
    source = item.get('source') or (item.get('inherits') or {}).get('source')

    if raw_kind in {'bookData', 'adventureData', 'book', 'adventure'}:
        return (_norm(item.get('id')), _norm(name), _norm(source))
    if raw_kind == 'itemProperty':
        return (_norm(item.get('abbreviation')), _norm(name), _norm(source))
    if raw_kind == 'subclass':
        return (
            _norm(name),
            _norm(item.get('shortName')),
            _norm(source),
            _norm(item.get('className')),
            _norm(item.get('classSource')),
        )
    if raw_kind == 'subclassFeature':
        return (
            _norm(name),
            _norm(source),
            _norm(item.get('className')),
            _norm(item.get('classSource')),
            _norm(item.get('subclassShortName')),
            _norm(item.get('subclassSource')),
            _norm(item.get('level')),
        )
    if raw_kind == 'classFeature':
        return (
            _norm(name),
            _norm(source),
            _norm(item.get('className')),
            _norm(item.get('classSource')),
            _norm(item.get('level')),
        )
    if raw_kind == 'subrace':
        return (
            _norm(name),
            _norm(source),
            _norm(item.get('raceName')),
            _norm(item.get('raceSource')),
        )
    return (_norm(name), _norm(eng_name), _norm(source))


def _detect_package_duplicates(
    package_id: str,
    doc: dict,
) -> tuple[list[dict], list[dict]]:
    """Detect natural-key duplicates and exact JSON duplicates within a single package."""
    key_dups: list[dict] = []
    exact_dups: list[dict] = []

    for raw_kind, rows in doc.items():
        if raw_kind == '_meta' or not isinstance(rows, list):
            continue
        key_counts: Counter[tuple[Any, ...]] = Counter()
        exact_counts: Counter[str] = Counter()
        sample_names: dict[tuple[Any, ...], Any] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            nkey = _raw_record_natural_key(raw_kind, item)
            key_counts[nkey] += 1
            if nkey not in sample_names:
                sample_names[nkey] = item.get('name') or item.get('id') or item.get('abbreviation')
            exact_counts[_canonical_json(item)] += 1

        for nkey, count in key_counts.items():
            if count > 1:
                key_dups.append({
                    'package_id': package_id,
                    'raw_kind': raw_kind,
                    'key': list(nkey),
                    'sample_name': sample_names.get(nkey),
                    'count': count,
                })
        for sig, count in exact_counts.items():
            if count > 1:
                exact_dups.append({
                    'package_id': package_id,
                    'raw_kind': raw_kind,
                    'count': count,
                    'sha256': _sha256_bytes(sig.encode('utf-8'))[:16],
                })
    return key_dups, exact_dups


def _merge_package_sources(
    book_sources: list[dict] | None,
    meta_sources: list[dict] | None,
    tiers: dict[str, str],
) -> list[dict]:
    """Merge source metadata from __api/books.json and _meta.sources without losing fields."""
    by_json: dict[str, dict] = {}
    order: list[str] = []

    for src in (meta_sources or []):
        if not isinstance(src, dict) or not src.get('json'):
            continue
        code = str(src['json'])
        entry = dict(src)
        if code in tiers:
            entry['tier'] = tiers[code]
        by_json[code] = entry
        order.append(code)

    for src in (book_sources or []):
        if not isinstance(src, dict) or not src.get('json'):
            continue
        code = str(src['json'])
        if code not in by_json:
            entry = dict(src)
            if code in tiers:
                entry['tier'] = tiers[code]
            by_json[code] = entry
            order.append(code)
        else:
            merged = by_json[code]
            for k, v in src.items():
                if v is not None and (k not in merged or merged[k] in (None, '', [])):
                    merged[k] = v
            if code in tiers:
                merged['tier'] = tiers[code]

    return [by_json[code] for code in order]


def load_inventory(source_root: Path) -> Inventory:
    """Scan, validate, and reconstruct a complete Inventory from an our5etools snapshot."""
    root = Path(source_root).expanduser().resolve()

    # 1. Validate required root layout
    data_dir = root / 'data'
    books_api_path = root / '__api/books.json'
    recipes_all_path = root / 'homebrew/_recipes/all.json'
    if not data_dir.is_dir():
        raise FileNotFoundError(f'Missing required official data directory: {data_dir}')
    if not books_api_path.is_file():
        raise FileNotFoundError(f'Missing required brew catalog: {books_api_path}')
    if not recipes_all_path.is_file():
        raise FileNotFoundError(f'Missing required homebrew recipe index: {recipes_all_path}')

    # 2. Load site brew declarations from homebrew/index.json['toImport']
    homebrew_index_path = root / 'homebrew/index.json'
    site_brew_relnames: list[str] = []
    if homebrew_index_path.is_file():
        hb_index = _read_json_bytes(homebrew_index_path)
        for name in hb_index.get('toImport') or []:
            checked = _checked_relative(str(name))
            site_brew_relnames.append(checked.as_posix())
    site_brew_paths = {f'homebrew/{name}' for name in site_brew_relnames}

    # 3. Parse official sources from __ours/assets.json['/js/parser.js'] (fallback: js/parser.js)
    assets_path = root / '__ours/assets.json'
    parser_js_text = ''
    if assets_path.is_file():
        assets_data = _read_json_bytes(assets_path)
        if isinstance(assets_data, dict) and isinstance(assets_data.get('/js/parser.js'), str):
            parser_js_text = assets_data['/js/parser.js']
    if not parser_js_text:
        disk_parser = root / 'js/parser.js'
        if disk_parser.is_file():
            parser_js_text = disk_parser.read_text(encoding='utf-8')
    official_sources, official_source_casefold, parser_warnings = parse_official_sources(parser_js_text)

    # 4. Enumerate every file under source_root (skipping .git/, resource-export/, symlinks)
    file_records: list[dict] = []
    media_files: dict[str, dict] = {}
    official_docs: list[dict] = []
    disposition_counts: Counter[str] = Counter()
    media_counts: Counter[str] = Counter()
    skipped_symlinks = 0

    all_paths: list[tuple[PurePosixPath, Path]] = []
    for path in root.rglob('*'):
        rel_path = path.relative_to(root)
        parts = rel_path.parts
        if not parts or parts[0] in SKIP_TOP_DIRS:
            continue
        if path.is_symlink():
            skipped_symlinks += 1
            continue
        if path.is_file():
            all_paths.append((PurePosixPath(rel_path.as_posix()), path))
    all_paths.sort(key=lambda item: item[0].as_posix())

    input_hasher = hashlib.sha256()
    file_sha_map: dict[str, str] = {}

    for rel, abs_path in all_paths:
        posix = rel.as_posix()
        raw_bytes = abs_path.read_bytes()
        size = len(raw_bytes)
        digest = _sha256_bytes(raw_bytes)
        file_sha_map[posix] = digest
        input_hasher.update(f'{posix}\0{digest}\n'.encode('utf-8'))

        category, role, disposition, reason = _classify_file(rel, site_brew_paths)
        disposition_counts[disposition] += 1

        file_records.append({
            'path': posix,
            'size': size,
            'sha256': digest,
            'category': category,
            'role': role,
            'disposition': disposition,
            'reason': reason,
        })

        # Index official JSON documents
        if disposition in {'entity_data', 'fluff_data', 'book_data', 'adventure_data'}:
            doc = json.loads(raw_bytes)
            official_docs.append({
                'path': posix,
                'disposition': disposition,
                'role': role,
                'package_id': 'official',
                'data': doc,
            })

        # Index all local audio / font / image files
        ext = rel.suffix.lower()
        if disposition == 'local_media' or ext in (AUDIO_EXTS | FONT_EXTS | IMAGE_EXTS):
            if ext in AUDIO_EXTS:
                media_kind = 'audio'
            elif ext in FONT_EXTS:
                media_kind = 'font'
            else:
                media_kind = 'image'
            media_counts[media_kind] += 1
            media_record: dict[str, Any] = {
                'path': posix,
                'kind': media_kind,
                'size': size,
                'sha256': digest,
                'ext': ext,
                'mime': _guess_mime(ext),
            }
            if media_kind == 'audio' and posix.startswith('audio/'):
                media_record['audio_subpath'] = posix.removeprefix('audio/')
            media_files[posix] = media_record

    # 5. Build snapshot_info
    status_api_path = root / '__api/status.json'
    stats_api_path = root / '__api/stats.json'
    status_api = _read_json_bytes(status_api_path) if status_api_path.is_file() else {}
    stats_api = _read_json_bytes(stats_api_path) if stats_api_path.is_file() else {}

    snapshot_info: dict[str, Any] = {
        'root': str(root),
        'git_commit': _git_commit(root),
        'app': status_api.get('app'),
        'version': status_api.get('version'),
        'build': status_api.get('build'),
        'ready': status_api.get('ready'),
        'brew_built': status_api.get('brew_built'),
        'data_date': status_api.get('data_date'),
        'brew_books': status_api.get('brew_books'),
        'our_books': status_api.get('ourBooks'),
        'status_api': status_api,
        'stats_api': stats_api,
        'input_sha256': input_hasher.hexdigest(),
        'file_count': len(file_records),
    }

    # 6. Load ratings & tiers
    tiers_path = root / '__api/tiers.json'
    api_ratings_path = root / '__api/ratings.json'
    ours_ratings_path = root / '__ours/ratings.json'
    tiers: dict[str, str] = _read_json_bytes(tiers_path) if tiers_path.is_file() else {}
    api_ratings = _read_json_bytes(api_ratings_path) if api_ratings_path.is_file() else {}
    ours_ratings = _read_json_bytes(ours_ratings_path) if ours_ratings_path.is_file() else {}
    ratings = {
        'tiers': tiers,
        'api_ratings': api_ratings,
        'ours_ratings': ours_ratings,
        'ratings_base_matches_ours': (
            api_ratings.get('base') == ours_ratings
            if isinstance(api_ratings, dict) and 'base' in api_ratings
            else None
        ),
    }

    # 7. Load generated_lookups
    generated_lookups: dict[str, Any] = {}
    missing_generated_lookups: list[str] = []
    for rel_lookup in GENERATED_LOOKUP_PATHS:
        lookup_path = root / rel_lookup
        if lookup_path.is_file():
            generated_lookups[rel_lookup] = _read_json_bytes(lookup_path)
        else:
            missing_generated_lookups.append(rel_lookup)

    # 8. Reconstruct all 321 brew packages + site packages
    api_books_list: list[dict] = _read_json_bytes(books_api_path)
    books_by_id: dict[str, dict] = {str(b['id']): b for b in api_books_list}
    recipe_all: dict[str, Any] = _read_json_bytes(recipes_all_path)
    recipe_docs: dict[str, list[str]] = recipe_all.get('docs') or {}
    recipe_packs: dict[str, str] = recipe_all.get('packs') or {}

    book_ids = set(books_by_id.keys())
    recipe_ids = set(recipe_docs.keys())
    missing_in_recipes = sorted(book_ids - recipe_ids)
    missing_in_books = sorted(recipe_ids - book_ids)
    books_recipe_keys_match = (book_ids == recipe_ids)

    loaded_shards: dict[str, dict[str, Any]] = {}
    missing_shard_files: set[str] = set()
    missing_shard_keys: list[dict] = []
    invalid_shard_paths: list[dict] = []
    count_mismatches: list[dict] = []
    duplicate_raw_records: list[dict] = []
    exact_duplicate_raw_records: list[dict] = []
    declared_dependencies: list[dict] = []
    declared_includes: list[dict] = []

    packages: list[dict] = []
    package_docs: dict[str, dict] = {}
    known_brew_sources_casefold: set[str] = set()

    # Follow __api/books.json order for determinism, plus any extra recipe keys if present.
    ordered_book_ids = [str(b['id']) for b in api_books_list] + missing_in_books

    for book_id in ordered_book_ids:
        book_meta = books_by_id.get(book_id) or {'id': book_id, 'file': book_id}
        refs = recipe_docs.get(book_id) or []
        package_id = f'brew:{book_id}'

        doc: dict[str, Any] = {}
        used_shards: set[str] = set()

        for ref in refs:
            raw_pack = recipe_packs.get(ref)
            if not raw_pack:
                missing_shard_keys.append({
                    'package_id': package_id,
                    'book_id': book_id,
                    'ref': ref,
                    'shard': None,
                    'reason': 'ref_not_in_packs_map',
                })
                continue
            try:
                shard_rel = _checked_relative(raw_pack.removeprefix('/'))
            except ValueError as exc:
                invalid_shard_paths.append({
                    'package_id': package_id,
                    'book_id': book_id,
                    'ref': ref,
                    'raw_pack': raw_pack,
                    'error': str(exc),
                })
                continue
            if shard_rel.parts[:2] != ('homebrew', '_data'):
                invalid_shard_paths.append({
                    'package_id': package_id,
                    'book_id': book_id,
                    'ref': ref,
                    'raw_pack': raw_pack,
                    'error': 'Shard outside homebrew/_data/',
                })
                continue

            shard_posix = shard_rel.as_posix()
            used_shards.add(shard_posix)
            if shard_posix not in loaded_shards:
                shard_abs = root / shard_posix
                if not shard_abs.is_file():
                    missing_shard_files.add(shard_posix)
                    loaded_shards[shard_posix] = {}
                else:
                    loaded_shards[shard_posix] = _read_json_bytes(shard_abs)

            shard_data = loaded_shards[shard_posix]
            if ref not in shard_data:
                missing_shard_keys.append({
                    'package_id': package_id,
                    'book_id': book_id,
                    'ref': ref,
                    'shard': shard_posix,
                    'reason': 'key_missing_in_shard',
                })
                continue

            key, value = shard_data[ref]
            if isinstance(value, list):
                doc.setdefault(key, []).extend(value)
            else:
                doc[key] = value

        meta = doc.get('_meta') if isinstance(doc.get('_meta'), dict) else {}
        expected_counts = dict(book_meta.get('counts') or {})
        actual_counts = {
            k: len(v)
            for k, v in doc.items()
            if k != '_meta' and isinstance(v, list)
        }
        counts_match = (actual_counts == expected_counts)
        if not counts_match:
            diffs = {
                k: {'expected': expected_counts.get(k, 0), 'actual': actual_counts.get(k, 0)}
                for k in sorted(set(expected_counts) | set(actual_counts))
                if expected_counts.get(k, 0) != actual_counts.get(k, 0)
            }
            count_mismatches.append({
                'package_id': package_id,
                'book_id': book_id,
                'diffs': diffs,
            })

        merged_sources = _merge_package_sources(
            book_meta.get('sources'),
            meta.get('sources'),
            tiers,
        )
        for src in merged_sources:
            if src.get('json'):
                known_brew_sources_casefold.add(str(src['json']).casefold())

        deps = meta.get('dependencies') if isinstance(meta.get('dependencies'), dict) else {}
        incs = meta.get('includes') if isinstance(meta.get('includes'), dict) else {}
        if deps:
            declared_dependencies.append({'package_id': package_id, 'book_id': book_id, 'dependencies': deps})
        if incs:
            declared_includes.append({'package_id': package_id, 'book_id': book_id, 'includes': incs})

        kdups, edups = _detect_package_duplicates(package_id, doc)
        duplicate_raw_records.extend(kdups)
        exact_duplicate_raw_records.extend(edups)

        rel_file = str(book_meta.get('file') or book_id)
        pkg_tiers = {
            str(s['json']): tiers[str(s['json'])]
            for s in merged_sources
            if s.get('json') and str(s['json']) in tiers
        }
        packages.append({
            'package_id': package_id,
            'kind': 'brew',
            'book_id': book_id,
            'file': rel_file,
            'virtual_path': f'homebrew/{rel_file}',
            'sha': book_meta.get('sha'),
            'ours': bool(book_meta.get('ours', False)),
            'edition': book_meta.get('edition') or meta.get('edition'),
            'groups': list(book_meta.get('groups') or []),
            'sources': merged_sources,
            'tiers': pkg_tiers,
            'expected_counts': expected_counts,
            'actual_counts': actual_counts,
            'counts_match': counts_match,
            'shard_refs': list(refs),
            'shard_files': sorted(used_shards),
            'meta': meta,
            'dependencies': deps,
            'includes': incs,
        })
        package_docs[package_id] = doc

    # Site packages from homebrew/index.json['toImport']
    missing_site_packages: list[str] = []
    site_package_count = 0
    for relname in site_brew_relnames:
        rel_posix = f'homebrew/{relname}'
        abs_site = root / rel_posix
        if not abs_site.is_file():
            missing_site_packages.append(rel_posix)
            continue
        doc = _read_json_bytes(abs_site)
        package_id = f'site:{rel_posix}'
        meta = doc.get('_meta') if isinstance(doc.get('_meta'), dict) else {}
        merged_sources = _merge_package_sources(None, meta.get('sources'), tiers)
        for src in merged_sources:
            if src.get('json'):
                known_brew_sources_casefold.add(str(src['json']).casefold())

        actual_counts = {
            k: len(v)
            for k, v in doc.items()
            if k != '_meta' and isinstance(v, list)
        }
        deps = meta.get('dependencies') if isinstance(meta.get('dependencies'), dict) else {}
        incs = meta.get('includes') if isinstance(meta.get('includes'), dict) else {}
        if deps:
            declared_dependencies.append({'package_id': package_id, 'book_id': relname, 'dependencies': deps})
        if incs:
            declared_includes.append({'package_id': package_id, 'book_id': relname, 'includes': incs})

        kdups, edups = _detect_package_duplicates(package_id, doc)
        duplicate_raw_records.extend(kdups)
        exact_duplicate_raw_records.extend(edups)

        pkg_tiers = {
            str(s['json']): tiers[str(s['json'])]
            for s in merged_sources
            if s.get('json') and str(s['json']) in tiers
        }
        packages.append({
            'package_id': package_id,
            'kind': 'site',
            'book_id': relname,
            'file': relname,
            'virtual_path': rel_posix,
            'sha': file_sha_map.get(rel_posix),
            'ours': True,
            'edition': meta.get('edition'),
            'groups': [],
            'sources': merged_sources,
            'tiers': pkg_tiers,
            'expected_counts': None,
            'actual_counts': actual_counts,
            'counts_match': True,
            'shard_refs': [],
            'shard_files': [rel_posix],
            'meta': meta,
            'dependencies': deps,
            'includes': incs,
        })
        package_docs[package_id] = doc
        site_package_count += 1

    # 9. Resolve _meta.dependencies & _meta.includes targets against official + brew/site sources
    all_known_sources = set(official_source_casefold.keys()) | known_brew_sources_casefold

    def _find_unresolved(records: list[dict], field: str) -> list[dict]:
        unresolved: list[dict] = []
        for entry in records:
            mapping = entry[field]
            for raw_kind, target_sources in mapping.items():
                if not isinstance(target_sources, list):
                    continue
                for target in target_sources:
                    if str(target).casefold() not in all_known_sources:
                        unresolved.append({
                            'package_id': entry['package_id'],
                            'book_id': entry['book_id'],
                            'raw_kind': raw_kind,
                            'target_source': target,
                        })
        return unresolved

    unresolved_dependencies = _find_unresolved(declared_dependencies, 'dependencies')
    unresolved_includes = _find_unresolved(declared_includes, 'includes')

    brew_package_count = sum(1 for p in packages if p['kind'] == 'brew')

    checks: dict[str, Any] = {
        'books_recipe_keys_match': books_recipe_keys_match,
        'missing_in_recipes': missing_in_recipes,
        'missing_in_books': missing_in_books,
        'missing_shard_files': sorted(missing_shard_files),
        'missing_shard_keys': missing_shard_keys,
        'invalid_shard_paths': invalid_shard_paths,
        'missing_site_packages': missing_site_packages,
        'count_mismatches': count_mismatches,
        'dependencies': declared_dependencies,
        'includes': declared_includes,
        'unresolved_dependencies': unresolved_dependencies,
        'unresolved_includes': unresolved_includes,
        'duplicate_raw_records': duplicate_raw_records,
        'exact_duplicate_raw_records': exact_duplicate_raw_records,
        'disposition_counts': dict(sorted(disposition_counts.items())),
        'media_counts': dict(sorted(media_counts.items())),
        'official_source_count': len(official_sources),
        'official_doc_count': len(official_docs),
        'brew_package_count': brew_package_count,
        'site_package_count': site_package_count,
        'skipped_symlinks': skipped_symlinks,
        'missing_generated_lookups': missing_generated_lookups,
        'parser_warnings': parser_warnings,
    }

    return Inventory(
        source_root=root,
        snapshot_info=snapshot_info,
        file_records=file_records,
        official_sources=official_sources,
        official_source_casefold=official_source_casefold,
        official_docs=official_docs,
        generated_lookups=generated_lookups,
        packages=packages,
        package_docs=package_docs,
        ratings=ratings,
        media_files=media_files,
        checks=checks,
    )
