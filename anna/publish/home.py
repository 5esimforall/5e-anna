"""Build the default home cache that the local server serves from data/home.json."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from anna.paths import BOOK, FIVETOOLS_DATA, OUR5ETOOLS_DATA, WEB
from anna.presentation.catalog import DB
from anna.server.app import Bundle, _try_load_fivetools, _try_load_our5etools, validate_bundle
from anna.server.home import LOCAL_CACHE, encode_home
from anna.server.query import QueryRequest


def build_local(our5etools_data, fivetools_data, *, web=WEB, lazy_media=False):
    if not (BOOK / 'anna-manifest.json').is_file():
        return  # Building the companion library can run before the default library exists.
    manifest = validate_bundle(web, DB, BOOK)
    bundle = Bundle(web, DB, BOOK, manifest, ())
    collections = (_try_load_our5etools(our5etools_data, lazy_media=lazy_media),
                   _try_load_fivetools(fivetools_data, lazy_media=lazy_media))
    registry = bundle.registry(tuple(c for c in collections if c is not None))

    payload = encode_home(QueryRequest(registry).handle_path)
    with TemporaryDirectory(dir=LOCAL_CACHE.parent) as staging:
        temporary = Path(staging) / LOCAL_CACHE.name
        temporary.write_bytes(payload)
        temporary.replace(LOCAL_CACHE)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web', type=Path, default=WEB)
    parser.add_argument('--our5etools-data', type=Path, default=OUR5ETOOLS_DATA)
    parser.add_argument('--5etools-data', dest='fivetools_data', type=Path, default=FIVETOOLS_DATA)
    parser.add_argument('--lazy-media', action='store_true')
    args = parser.parse_args()
    build_local(args.our5etools_data, args.fivetools_data, web=args.web, lazy_media=args.lazy_media)
