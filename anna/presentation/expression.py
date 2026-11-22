"""The conditions a query sets, as the reader wrote them: a line of cards joined by ∧ and ∨, grouped by parentheses.

Neighbouring cards join with ∧ unless the reader turned the join to ∨. ∨ binds tighter than ∧, so `A ∧ B ∨ C ∧ D`
reads A ∧ (B ∨ C) ∧ D: turning one join widens that condition into alternatives and leaves the others required,
which is how a reader adds an alternative to a line of requirements. Parentheses regroup. A card's own values are
alternatives already, so a line without ∨ or parentheses is a conjunction of cards, as every query used to be.

A request writes the line as the order of its parameters. Consecutive values of one field form one card; each search
term (`q`, `body`, their exclusions and the `typing` draft) is a card of its own. Marker parameters stand between
cards: `or` joins the next card with ∨, `and` keeps ∧ and separates two cards of one field, `open` and `close` are
the parentheses. `probe` marks where the reader's caret stands while a value is being chosen there. An unclosed `(`
closes at the end of the line and a stray `)` opens at its start. Every other parameter is an option of the request
(sort, paging, sections, …), read as before.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace

from anna.catalog.views import NONE

NOT = 'not_'
OR, AND, OPEN, CLOSE, PROBE_KEY = 'or', 'and', 'open', 'close', 'probe'
MARKERS = frozenset({OR, AND, OPEN, CLOSE})
# Each value of these is a card of its own; values of any other condition key gather into one card per run.
TERMS = frozenset({'q', 'body', 'typing', NOT + 'q', NOT + 'body'})
FIELDS = frozenset({'type', 'cr_min', 'cr_max', 'conflict', 'ruleset', 'legacy', 'status', 'source', 'origin',
                    'package', 'copy_status'})
NEGATABLE = frozenset({'type', 'q', 'body', 'ruleset', 'legacy', 'status', 'source', 'origin', 'package', 'copy_status'})
CARD_LIMIT = 60


def card_key(key: str) -> bool:
    """Whether a parameter writes a card. Excluding a condition that cannot be excluded is an error."""
    if key in TERMS or key in FIELDS or key.startswith('f_'):
        return True
    if key.startswith(NOT):
        base = key[len(NOT):]
        if base in NEGATABLE or base.startswith('f_'):
            return True
        raise ValueError('无法排除：' + base)
    return False


@dataclass(frozen=True)
class Card:
    """One condition the reader wrote. `index` is its place among the line's cards, which is how counts name it."""
    index: int
    key: str
    values: tuple[str, ...]

    @property
    def base(self) -> str:
        return self.key.removeprefix(NOT)

    @property
    def negated(self) -> bool:
        return self.key.startswith(NOT)

    @property
    def field(self) -> str:
        """The field the card constrains: the draft being typed searches names, like a kept search."""
        return 'q' if self.base == 'typing' else self.base


@dataclass(frozen=True)
class Probe:
    """The caret, where a value is about to be chosen: counts say what each value would match placed there."""


PROBE = Probe()


@dataclass(frozen=True)
class AllOf:
    parts: tuple


@dataclass(frozen=True)
class AnyOf:
    parts: tuple


TRUE, FALSE = AllOf(()), AnyOf(())
Node = Card | Probe | AllOf | AnyOf


def every(parts: Iterable[Node]) -> Node:
    """∧ of the parts: TRUE parts drop out, a FALSE part decides."""
    flat = []
    for p in parts:
        if p == FALSE:
            return FALSE
        flat.extend(p.parts if isinstance(p, AllOf) else [p])
    return flat[0] if len(flat) == 1 else AllOf(tuple(flat))


def either(parts: Iterable[Node]) -> Node:
    """∨ of the parts: FALSE parts drop out, a TRUE part decides."""
    flat = []
    for p in parts:
        if p == TRUE:
            return TRUE
        flat.extend(p.parts if isinstance(p, AnyOf) else [p])
    return flat[0] if len(flat) == 1 else AnyOf(tuple(flat))


def read(pairs: Iterable[tuple[str, str]]) -> tuple[Node, dict[str, list[str]]]:
    """The line a request writes, and its options. Blank values set nothing, as in every other parameter."""
    tokens: list[tuple] = []
    options: dict[str, list[str]] = {}
    joined_or, split, count = False, True, 0
    for key, value in pairs:
        if key in MARKERS:
            if key == OPEN:
                tokens.append(('open', joined_or))
            elif key == CLOSE:
                tokens.append(('close',))
            joined_or, split = key == OR, True
            continue
        if key == PROBE_KEY:
            if not any(t[0] == 'probe' for t in tokens):
                tokens.append(('probe', PROBE, joined_or))
            joined_or, split = False, True
            continue
        if not card_key(key):
            if value:
                options.setdefault(key, []).append(value)
            continue
        if not (value.strip() if key in TERMS else value):
            continue
        last = tokens[-1] if tokens else None
        if not split and key not in TERMS and last and last[0] == 'card' and last[1].key == key:
            tokens[-1] = ('card', replace(last[1], values=(*last[1].values, value)), last[2])
        else:
            if count == CARD_LIMIT:
                raise ValueError('条件过多')
            tokens.append(('card', Card(count, key, (value,)), joined_or))
            count += 1
        joined_or, split = False, False
    return _tree(tokens), options


def _tree(tokens: list[tuple]) -> Node:
    # A stray `)` opens at the start of the line and an unclosed `(` closes at its end.
    depth = stray = 0
    for t in tokens:
        if t[0] == 'open':
            depth += 1
        elif t[0] == 'close':
            depth, stray = (depth - 1, stray) if depth else (depth, stray + 1)
    tokens = [('open', False)] * stray + tokens + [('close',)] * depth
    at = 0

    def starts() -> bool:
        return at < len(tokens) and tokens[at][0] != 'close'

    def atom():
        nonlocal at
        token = tokens[at]
        at += 1
        if token[0] != 'open':
            return token[1]
        inner = conjunction()
        at += 1  # the matching `)`; the line is balanced
        return inner

    def alternatives():
        # A part joined to the one before it by ∨ belongs to the same alternatives.
        parts = [atom()]
        while starts() and tokens[at][-1]:
            parts.append(atom())
        kept = [p for p in parts if p is not None]
        return either(kept) if kept else None

    def conjunction():
        parts = []
        while starts():
            parts.append(alternatives())
        kept = [p for p in parts if p is not None]
        # An empty group, `( )`, stands for nothing rather than for a condition every entry meets.
        return every(kept) if kept else None

    tree = conjunction()
    return TRUE if tree is None else tree


def cards(node: Node) -> Iterator[Card]:
    if isinstance(node, Card):
        yield node
    elif isinstance(node, (AllOf, AnyOf)):
        for p in node.parts:
            yield from cards(p)


def has_probe(node: Node) -> bool:
    return node is PROBE or isinstance(node, (AllOf, AnyOf)) and any(has_probe(p) for p in node.parts)


def substitute(node: Node, leaf: Callable[[Card | Probe], Node]) -> Node:
    """The tree with each card (and the probe) replaced by what `leaf` returns, constants folded."""
    if isinstance(node, (Card, Probe)):
        return leaf(node)
    parts = [substitute(p, leaf) for p in node.parts]
    return every(parts) if isinstance(node, AllOf) else either(parts)


def relax(node: Node, aside: Callable[[Card], bool]) -> Node:
    """The tree with the cards `aside` picks set aside: each holds for every entry, as a count's own field does."""
    return substitute(node, lambda c: TRUE if isinstance(c, Card) and aside(c) else c)


def settle(node: Node) -> Node:
    """The tree with cards left without any value folded: an included one holds for nothing, an excluded one for all."""
    return substitute(node, lambda c: (TRUE if c.negated else FALSE) if isinstance(c, Card) and c.values == (NONE,) else c)


def without(node: Node, index: int | None) -> Node:
    """The tree once a card is removed from the line (the probe when `index` is None): it leaves its group, and a group
    left empty leaves its own. Removing one of several alternatives narrows the query; removing a requirement widens it."""
    def go(n):
        if isinstance(n, Card):
            return None if n.index == index else n
        if isinstance(n, Probe):
            return None if index is None else n
        parts = [p for p in (go(p) for p in n.parts) if p is not None]
        return (every(parts) if isinstance(n, AllOf) else either(parts)) if parts else None
    out = go(node)
    return TRUE if out is None else out


def open_types(node: Node) -> bool:
    """Whether entries of any type can match: with every card but the types set aside, nothing is left to meet.
    `(类型：法术 ∧ 学派：塑能) ∨ 名称：龙` lists dragons of every type beside the evocations."""
    return relax(node, lambda c: c.field != 'type') == TRUE


def required(node: Node, index: int) -> bool:
    """Whether the card must hold for every match on its own, so removing it is the same as setting it aside."""
    parts = node.parts if isinstance(node, AllOf) else (node,)
    return any(isinstance(p, Card) and p.index == index for p in parts)


def sql(node: Node, condition: Callable[[Card], tuple[str, list]]) -> tuple[str, list]:
    """SQL for the tree, with `condition` giving each card's own. A card left without any value matches nothing, or,
    excluded, excludes nothing."""
    if node == TRUE:
        return '1', []
    if node == FALSE:
        return '0', []
    if isinstance(node, Card):
        if node.values == (NONE,):
            return ('1' if node.negated else '0'), []
        text, values = condition(node)
        return f'({text})', list(values)
    if isinstance(node, Probe):
        raise ValueError('探针不能直接查询')
    parts = [sql(p, condition) for p in node.parts]
    joiner = ' AND ' if isinstance(node, AllOf) else ' OR '
    return '(' + joiner.join(text for text, _ in parts) + ')', [v for _, values in parts for v in values]


def test(node: Node, leaf: Callable[[Card | Probe], Callable[[object], bool]]) -> Callable[[object], bool]:
    """A function telling whether a row meets the tree, given a test for each card (and the probe)."""
    if node == TRUE:
        return lambda row: True
    if node == FALSE:
        return lambda row: False
    if isinstance(node, (Card, Probe)):
        return leaf(node)
    parts = [test(p, leaf) for p in node.parts]
    if isinstance(node, AllOf):
        return lambda row: all(p(row) for p in parts)
    return lambda row: any(p(row) for p in parts)


class Query(Mapping):
    """A request: the line of conditions as written (`tree`, with the probe where the caret stands) and its options.

    As a mapping it reads like the request's parameters: each condition key lists the values of all its cards (a
    value left out for a collection, `__none__`, reads as absent), each option its values. Code that needs only the
    chosen types or the latest search reads it that way; code that filters reads the tree."""

    def __init__(self, tree: Node, options: Mapping[str, list[str]]):
        self.tree = tree
        self.probing = has_probe(tree)
        # The conditions that decide which entries match: the line without the probe.
        self.where = without(tree, None) if self.probing else tree
        self.options = {k: list(v) for k, v in options.items()}
        view: dict[str, list[str]] = {}
        for card in cards(tree):
            for v in card.values:
                if v != NONE:
                    view.setdefault(card.key, []).append(v)
        self._view = {**view, **self.options}

    @classmethod
    def parse(cls, pairs: Iterable[tuple[str, str]]) -> 'Query':
        tree, options = read(pairs)
        return cls(tree, options)

    @classmethod
    def of(cls, params: Mapping[str, list[str]]) -> 'Query':
        """A query from request parameters; a plain mapping lists one card per condition key, all required."""
        if isinstance(params, Query):
            return params
        return cls.parse((k, v) for k, values in params.items() for v in values)

    def cards(self) -> list[Card]:
        return list(cards(self.tree))

    def with_cards(self, change: Callable[[Card], Card]) -> 'Query':
        """The same line with each card changed in place, keeping its index (a collection's local values)."""
        return Query(substitute(self.tree, lambda c: change(c) if isinstance(c, Card) else c), self.options)

    def updated(self, options: Mapping[str, list[str]]) -> 'Query':
        return Query(self.tree, {**self.options, **options})

    def key(self) -> tuple:
        """A value naming this query for caches: its line and its options."""
        return (self.tree, tuple(sorted((k, tuple(v)) for k, v in self.options.items())))

    def __getitem__(self, key):
        return self._view[key]

    def __iter__(self):
        return iter(self._view)

    def __len__(self):
        return len(self._view)

    def __repr__(self):
        return f'Query({self.tree!r}, {self.options!r})'
