"""Shared project paths; WEB_DIR overrides the sibling read-only source mirror."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get('WEB_DIR') or PROJECT.parent / '5echm-web').expanduser().resolve()
CACHE = PROJECT / '.cache' / '5echm-search.pkl'
