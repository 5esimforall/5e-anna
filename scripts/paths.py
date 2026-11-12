"""Shared project paths; WEB_DIR and ETOOLS_DIR override the sibling read-only mirrors."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get('WEB_DIR') or PROJECT.parent / '5echm-web').expanduser().resolve()
# 5etools-cn, compared with anna's entries as a second, independently curated catalog.
ETOOLS = Path(os.environ.get('ETOOLS_DIR') or PROJECT.parent / '5etools-cn').expanduser().resolve()
CACHE = PROJECT / '.cache' / '5echm-search.pkl'
