"""Serialize the work alignment shipped with a pair of Pages snapshots."""
from dataclasses import asdict
import json

from anna.presentation.alignment import Alignment, Link
from anna.presentation.library import Publications, publications_of

PATH = 'data/publications.json'
SCHEMA = 1


def build_publications(path, registry):
    publications = publications_of(registry)
    alignment = publications.alignment
    data = {
        'schema': SCHEMA,
        'snapshots': {key: registry.get(key).snapshot for key in registry.ready()},
        'members': alignment.members,
        'links': [[left, right, asdict(link)] for (left, right), link in sorted(alignment.links.items())],
        'ranks': publications.ranks,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), sort_keys=True), encoding='utf-8')


def load_publications(path, snapshots):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != SCHEMA or data.get('snapshots') != snapshots:
        raise ValueError('Published work relationships do not match the library snapshots')
    members = {key: [tuple(work) for work in works] for key, works in data['members'].items()}
    clusters = {work: key for key, works in members.items() for work in works}
    links = {(tuple(left), tuple(right)): Link(**link) for left, right, link in data['links']}
    return Publications(Alignment(clusters, members, links), data['ranks'])
