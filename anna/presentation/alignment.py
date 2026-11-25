"""Which works in different collections are one publication.

A reader who reads several collections meets the same book more than once: The Griffon's Saddlebag Book 2 is
community homebrew in our5etools and a partnered publication in 不全书, each with its own translation. Each
collection keeps its own works, entries and evidence; an alignment only says that two works are the same
publication, so that the library can show their same-name entries as one object's texts, count both as the same
kind of content and let the reader choose whose text leads.

Works are aligned on names they share: a 5etools source code (the book's own codes come from its counterpart
votes), an abbreviation, or a title once editions in brackets, years and filler words are set aside. Several
candidates for one work are settled by the entries the works share (same type and English name); a pair is kept
only when each is the other's single best candidate, so an ambiguity leaves both works unaligned.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Any
import unicodedata

# Evidence from strongest to weakest.
RULES = ('code', 'abbreviation', 'title')
FILLER = frozenset({'the', 'a', 'an', 'of', 'and', 'book'})
BRACKETS = re.compile(r'[（(][^）)]*[）)]')
YEAR = re.compile(r'(?:19|20)\d\d')


def _fold(text: str) -> str:
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', text or '').casefold())


def title_key(title: str) -> str:
    """A title as two collections may both write it: brackets, years, punctuation and filler words dropped."""
    text = BRACKETS.sub('', unicodedata.normalize('NFKC', title or '')).casefold()
    words = [w for w in re.findall(r'[^\W_]+', text) if w not in FILLER and not YEAR.fullmatch(w)]
    return ''.join(words)


def keys_of(work: dict) -> dict[str, set[str]]:
    """The keys a work can be matched by, per rule."""
    return {
        'code': {c.casefold() for c in work.get('codes') or [] if c},
        'abbreviation': {k for k in map(_fold, work.get('abbreviations') or []) if len(k) >= 3},
        'title': {k for k in map(title_key, work.get('titles') or []) if len(k) >= 3},
    }


@dataclass(frozen=True)
class Link:
    """Why two works are one publication: the strongest shared name and the entries they share."""

    rule: str
    key: str
    votes: int


@dataclass
class Alignment:
    """Works of different collections that are one publication, gathered into clusters."""

    clusters: dict[tuple[str, str], str] = field(default_factory=dict)
    members: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    links: dict[tuple[tuple[str, str], tuple[str, str]], Link] = field(default_factory=dict)

    def cluster(self, collection: str, work: str | None) -> str | None:
        """The publication a work belongs to; an unaligned work is a publication of its own."""
        if not work:
            return None
        return self.clusters.get((collection, work)) or collection + ':' + work

    def counterparts(self, collection: str, work: str) -> list[tuple[str, str]]:
        """The same publication's works in other collections."""
        cluster = self.clusters.get((collection, work))
        return [m for m in self.members.get(cluster, []) if m[0] != collection] if cluster else []

    def link(self, a: tuple[str, str], b: tuple[str, str]) -> Link | None:
        return self.links.get((a, b)) or self.links.get((b, a))


class UnionFind:
    """Path-halving disjoint-set forest keyed by arbitrary hashable nodes."""

    def __init__(self) -> None:
        self.parent: dict[Any, Any] = {}

    def find(self, x: Any) -> Any:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: Any, b: Any) -> None:
        self.parent[self.find(a)] = self.find(b)


def align(sides: list[tuple[str, list[dict], dict[str, set]]]) -> Alignment:
    """Align the works of each pair of collections.

    Each side is (collection key, its works with identifiers, the (type, folded English name) pairs of each work's
    stackable entries)."""
    accepted: list[tuple[tuple[str, str], tuple[str, str], Link]] = []
    for i, (key_a, works_a, names_a) in enumerate(sides):
        for key_b, works_b, names_b in sides[i + 1:]:
            accepted.extend(_align_pair(key_a, works_a, names_a, key_b, works_b, names_b))
    out = Alignment()
    uf = UnionFind()
    for a, b, link in accepted:
        uf.union(a, b)
        out.links[(a, b)] = link
    groups: dict = defaultdict(list)
    for node in list(uf.parent):
        groups[uf.find(node)].append(node)
    for nodes in groups.values():
        nodes.sort()
        name = nodes[0][0] + ':' + nodes[0][1]
        out.members[name] = nodes
        for node in nodes:
            out.clusters[node] = name
    return out


def _align_pair(key_a, works_a, names_a, key_b, works_b, names_b):
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for work in works_b:
        for rule, keys in keys_of(work).items():
            for k in keys:
                index[(rule, k)].append(work['id'])
    candidates: dict[str, dict[str, Link]] = defaultdict(dict)   # work of A -> work of B -> strongest link
    for work in works_a:
        for rule, keys in keys_of(work).items():
            for k in keys:
                for other in index.get((rule, k), ()):
                    known = candidates[work['id']].get(other)
                    if known is None or RULES.index(rule) < RULES.index(known.rule):
                        votes = len(names_a.get(work['id'], set()) & names_b.get(other, set()))
                        candidates[work['id']][other] = Link(rule, k, votes)
    reverse: dict[str, dict[str, Link]] = defaultdict(dict)
    for a, links in candidates.items():
        for b, link in links.items():
            reverse[b][a] = link

    def best(links):
        ranked = sorted(links.items(), key=lambda kv: (RULES.index(kv[1].rule), -kv[1].votes, kv[0]))
        if len(ranked) > 1 and (RULES.index(ranked[0][1].rule), ranked[0][1].votes) == (RULES.index(ranked[1][1].rule), ranked[1][1].votes):
            return None
        return ranked[0][0]

    out = []
    for a, links in candidates.items():
        b = best(links)
        if b is not None and best(reverse[b]) == a:
            out.append(((key_a, a), (key_b, b), links[b]))
    return out
