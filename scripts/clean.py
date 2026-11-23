#!/usr/bin/env python3
"""Remove local generated files without following links into source repositories."""
import argparse
import os
from pathlib import Path
import shutil


GENERATED = ('data', 'output')
# Installed environments and download caches; removed only on request because reinstalling them is slow.
DEPENDENCIES = ('.cache', '.venv', 'node_modules')
ICONS = ('anna-logo.png', 'stella-logo.png', 'favicon-16.png', 'favicon-32.png',
         'favicon-48.png', 'favicon.ico', 'stella-favicon.ico')
UNSCANNED = {'.git', '.agents', '.codex', '.aws'}


def clean(project, dependencies=False):
    project = Path(project).resolve()
    removed = []

    def remove(path):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return
        removed.append(path.relative_to(project).as_posix())

    for name in GENERATED + (DEPENDENCIES if dependencies else ()):
        remove(project / name)
    for name in ICONS:
        remove(project / 'web' / name)
    for path in project.glob('.build-*'):
        remove(path)
    for directory, folders, files in os.walk(project, followlinks=False):
        kept = UNSCANNED | (set(DEPENDENCIES) if Path(directory) == project else set())
        folders[:] = [name for name in folders if name not in kept]
        for name in list(folders):
            if name == '__pycache__':
                remove(Path(directory) / name)
                folders.remove(name)
        for name in files:
            if name.endswith('.pyc') or name == '.DS_Store':
                remove(Path(directory) / name)
    return removed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependencies', action='store_true',
                        help='also remove .venv/, node_modules/ and the npm and Chromium caches in .cache/')
    args = parser.parse_args()
    for name in clean(Path(__file__).resolve().parents[1], dependencies=args.dependencies):
        print('Removed ' + name)
