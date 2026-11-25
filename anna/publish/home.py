"""Build the default home cache that the local server serves from data/home.json."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from anna.paths import BOOK, FIVETOOLS_DATA, OUR5ETOOLS_DATA, WEB
from anna.presentation.catalog import DB
from anna.server import tour_cache
from anna.server.app import AnnaHandler, Bundle, _try_load_fivetools, _try_load_our5etools, validate_bundle
from anna.server.home import (
    LOCAL_CACHE, LOCAL_SCOPES_CACHE, encode_home, encode_local_scopes,
)
from anna.server.query import CapturedResponse, QueryRequest


class _LocalCacheRequest(CapturedResponse, AnnaHandler):
    pass


def _write_atomic(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as staging:
        temporary = Path(staging) / target.name
        temporary.write_bytes(payload)
        temporary.replace(target)


def _write_cache_pair(handle, ready_keys, cache_path: Path, scopes_path: Path, encode_fn, encode_scopes_fn) -> None:
    _write_atomic(cache_path, encode_fn(handle))
    scopes_payload = encode_scopes_fn(handle, ready_keys)
    if scopes_payload is not None:
        _write_atomic(scopes_path, scopes_payload)
    else:
        scopes_path.unlink(missing_ok=True)


def build_local(our5etools_data, fivetools_data, *, web=WEB, lazy_media=False):
    if not (BOOK / 'anna-manifest.json').is_file():
        return  # Building the companion library can run before the default library exists.
    manifest = validate_bundle(web, DB, BOOK)
    bundle = Bundle(web, DB, BOOK, manifest, ())
    collections = (_try_load_our5etools(our5etools_data, lazy_media=lazy_media),
                   _try_load_fivetools(fivetools_data, lazy_media=lazy_media))
    registry = bundle.registry(tuple(c for c in collections if c is not None))
    ready_keys = registry.ready()

    _write_cache_pair(
        QueryRequest(registry).handle_path, ready_keys,
        LOCAL_CACHE, LOCAL_SCOPES_CACHE, encode_home, encode_local_scopes,
    )
    _write_cache_pair(
        _LocalCacheRequest(registry).dispatch, ready_keys,
        tour_cache.LOCAL_CACHE, tour_cache.LOCAL_SCOPES_CACHE, tour_cache.encode_tour, tour_cache.encode_local_scopes,
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web', type=Path, default=WEB)
    parser.add_argument('--our5etools-data', type=Path, default=OUR5ETOOLS_DATA)
    parser.add_argument('--5etools-data', dest='fivetools_data', type=Path, default=FIVETOOLS_DATA)
    parser.add_argument('--lazy-media', action='store_true')
    args = parser.parse_args()
    build_local(args.our5etools_data, args.fivetools_data, web=args.web, lazy_media=args.lazy_media)
