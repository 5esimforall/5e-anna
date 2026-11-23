"""Shared project paths; WEB_DIR, COUNTERPARTS_DIR and OUR5ETOOLS_DIR override local mirrors."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get('WEB_DIR') or PROJECT.parent / '5echm-web').expanduser().resolve()
# The counterparts mirror is 5etools-cn, compared with anna's entries as a second, independently curated catalog.
COUNTERPARTS = Path(os.environ.get('COUNTERPARTS_DIR') or PROJECT.parent / '5etools-cn').expanduser().resolve()
OUR5ETOOLS = Path(os.environ.get('OUR5ETOOLS_DIR') or PROJECT.parent / 'our5etools').expanduser().resolve()
OUR5ETOOLS_DATA = PROJECT / 'data' / 'our5etools'
FIVETOOLS_DATA = PROJECT / 'data' / '5etools'
