#!/usr/bin/env python3
"""Read and maintain sources.json, which pins the revision of every source mirror.

Local builds and the GitHub Pages workflow name the same snapshot through this lock. Mirrors are only
read here: their HEAD is compared with the lock or recorded into it, and nothing is checked out.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

LOCK = Path(__file__).resolve().parents[1] / 'sources.json'
SCHEMA = 1
REQUIRED = ('repo', 'branch', 'revision')
OPTIONAL = ('sparse',)
REVISION = re.compile(r'[0-9a-f]{40}')
WORD = re.compile(r'\S+')


class LockError(ValueError):
    pass


def load(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise LockError(f'{path}: source lock not found') from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LockError(f'{path}: unreadable source lock ({error})') from None
    if not isinstance(data, dict) or set(data) != {'schema', 'mirrors'} or data['schema'] != SCHEMA:
        raise LockError(f'{path}: expected {{"schema": {SCHEMA}, "mirrors": {{...}}}}')
    mirrors = data['mirrors']
    if not isinstance(mirrors, dict) or not mirrors:
        raise LockError(f'{path}: "mirrors" must name at least one mirror')
    for key, mirror in mirrors.items():
        if not isinstance(mirror, dict):
            raise LockError(f'{path}: mirror {key} must be an object')
        missing = [field for field in REQUIRED if field not in mirror]
        unknown = sorted(set(mirror) - set(REQUIRED) - set(OPTIONAL))
        if missing or unknown:
            raise LockError(f'{path}: mirror {key} is missing {missing} or has unknown fields {unknown}')
        for field in ('repo', 'branch'):
            if not isinstance(mirror[field], str) or not WORD.fullmatch(mirror[field]):
                raise LockError(f'{path}: mirror {key} needs a {field} without spaces')
        if not isinstance(mirror['revision'], str) or not REVISION.fullmatch(mirror['revision']):
            raise LockError(f'{path}: mirror {key} needs a full 40-character lowercase revision')
        sparse = mirror.get('sparse', [])
        if not isinstance(sparse, list) or not all(isinstance(p, str) and WORD.fullmatch(p) for p in sparse):
            raise LockError(f'{path}: mirror {key} needs sparse paths as a list of names without spaces')
    return data


def save(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.sources-',
                                     suffix='.json', delete=False) as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    os.replace(handle.name, path)


def git(path, *args):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def inspect(path):
    """Return the mirror's HEAD, or None with the reason it cannot be compared."""
    path = Path(path)
    if not path.exists():
        return None, f'mirror not present at {path}'
    # A directory without its own .git would otherwise report the HEAD of an enclosing repository.
    head = git(path, 'rev-parse', '--verify', '--quiet', 'HEAD') if (path / '.git').exists() else None
    if head is None:
        return None, f'{path} is not a Git checkout'
    return head, None


def compare(pin, path):
    head, reason = inspect(path)
    if head is None:
        return False, f'pinned {pin[:7]}, {reason}'
    if head == pin:
        return True, f'pinned {pin[:7]}, HEAD matches'
    return False, f'pinned {pin[:7]}, HEAD {head[:7]} differs'


def published(path, head, branch):
    remote = git(path, 'rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}')
    if remote is None:
        return False
    return remote == head or subprocess.run(['git', '-C', str(path), 'merge-base', '--is-ancestor', head, remote],
                                            capture_output=True).returncode == 0


def mirror_paths(data, pairs):
    selected = []
    for pair in pairs:
        key, separator, path = pair.partition('=')
        if not separator or not path:
            raise LockError(f'expected KEY=PATH, got {pair!r}')
        if key not in data['mirrors']:
            raise LockError(f'unknown mirror {key!r}; the lock names {", ".join(data["mirrors"])}')
        selected.append((key, Path(path)))
    return selected


def get(data, key, field):
    if key not in data['mirrors']:
        raise LockError(f'unknown mirror {key!r}; the lock names {", ".join(data["mirrors"])}')
    if field not in REQUIRED + OPTIONAL:
        raise LockError(f'unknown field {field!r}; use one of {", ".join(REQUIRED + OPTIONAL)}')
    value = data['mirrors'][key].get(field, [])
    return ' '.join(value) if isinstance(value, list) else value


def status(data, pairs):
    return [f'{key}: {compare(data["mirrors"][key]["revision"], path)[1]}' for key, path in pairs]


def check(data, pairs):
    """Warnings for mirrors that a build would read at a revision other than the pin."""
    warnings = []
    for key, path in pairs:
        matches, detail = compare(data['mirrors'][key]['revision'], path)
        if not matches:
            warnings.append(f'warning: {key}: {detail}; this build reads the mirror as it is, Pages builds the pin')
    return warnings


def record(data, pairs):
    """Pin each mirror's HEAD when its remote branch contains it; returns (changed, messages)."""
    changed, messages = False, []
    for key, path in pairs:
        mirror = data['mirrors'][key]
        pin = mirror['revision']
        head, reason = inspect(path)
        if head is None:
            messages.append(f'{key}: {reason}; pin {pin[:7]} unchanged')
        elif head == pin:
            messages.append(f'{key}: pin {pin[:7]} unchanged')
        elif not published(path, head, mirror['branch']):
            messages.append(f'{key}: HEAD {head[:7]} is not on origin/{mirror["branch"]}; pin {pin[:7]} unchanged')
        else:
            mirror['revision'] = head
            changed = True
            messages.append(f'{key}: pin {pin[:7]} -> {head[:7]}')
    return changed, messages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=LOCK)
    commands = parser.add_subparsers(dest='command', required=True)
    field = commands.add_parser('get', help='print one field of a mirror (sparse prints space-separated paths)')
    field.add_argument('key')
    field.add_argument('field')
    for name, text in (('status', 'describe each mirror against its pin'),
                       ('check', 'warn about mirrors whose HEAD differs from the pin; always succeeds'),
                       ('record', 'pin each mirror HEAD that its origin branch contains')):
        command = commands.add_parser(name, help=text)
        command.add_argument('mirrors', nargs='+', metavar='KEY=PATH')
    args = parser.parse_args(argv)
    try:
        data = load(args.lock)
        if args.command == 'get':
            print(get(data, args.key, args.field))
            return 0
        pairs = mirror_paths(data, args.mirrors)
    except LockError as error:
        print(f'sources: {error}', file=sys.stderr)
        return 1
    if args.command == 'status':
        print('\n'.join(status(data, pairs)))
    elif args.command == 'check':
        for warning in check(data, pairs):
            print(warning, file=sys.stderr)
    else:
        changed, messages = record(data, pairs)
        if changed:
            save(args.lock, data)
        print('\n'.join(messages))
    return 0


if __name__ == '__main__':
    sys.exit(main())
