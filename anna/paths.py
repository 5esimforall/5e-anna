"""Shared project paths; WEB_DIR, 5ETOOLS_DIR and OUR5ETOOLS_DIR override local mirrors."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get('WEB_DIR') or PROJECT.parent / '5echm-web').expanduser().resolve()
# 5etools-cn, compared with anna's entries as a second, independently curated catalog. A shell cannot
# assign 5ETOOLS_DIR directly; pass it as a make argument or with `env 5ETOOLS_DIR=...`.
FIVETOOLS = Path(os.environ.get('5ETOOLS_DIR') or PROJECT.parent / '5etools-cn').expanduser().resolve()


def _default_our5etools():
    env = os.environ.get('OUR5ETOOLS_DIR')
    if env:
        return Path(env).expanduser().resolve()
    for candidate in (
        PROJECT.parent / 'our5etools',
        Path('/Users/arctic/Box/Repo/KuroNeko233333.github.io'),
    ):
        if candidate.exists():
            return candidate.resolve()
    return (PROJECT.parent / 'our5etools').resolve()


OUR5ETOOLS = _default_our5etools()
OUR5ETOOLS_DATA = PROJECT / 'data' / 'our5etools'
