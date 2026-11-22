import unittest

from anna.presentation import expression
from anna.presentation.expression import AllOf, AnyOf, Card, Query


def line(*pairs):
    """A request's parameters as `key=value` or bare markers, in order."""
    return Query.parse(tuple(p.split('=', 1)) if '=' in p else (p, '') for p in pairs)


def shape(node):
    """A tree written back with ∧, ∨ and parentheses, each card as key:values."""
    if isinstance(node, Card):
        return f"{node.key}:{','.join(node.values)}"
    if node is expression.PROBE:
        return '?'
    if node == expression.TRUE:
        return 'TRUE'
    if node == expression.FALSE:
        return 'FALSE'
    inner = (' ∧ ' if isinstance(node, AllOf) else ' ∨ ').join(shape(p) for p in node.parts)
    return f'({inner})'


class ExpressionTests(unittest.TestCase):
    def test_or_binds_tighter_than_and(self):
        q = line('type=spell', 'f_school=塑能', 'or', 'q=火', 'f_spell_level=1')
        self.assertEqual(shape(q.where), '(type:spell ∧ (f_school:塑能 ∨ q:火) ∧ f_spell_level:1)')
        self.assertEqual(shape(line('q=a', 'or', 'q=b', 'or', 'q=c').where), '(q:a ∨ q:b ∨ q:c)')

    def test_parentheses_regroup_and_balance_at_the_line_ends(self):
        q = line('open', 'type=spell', 'f_school=塑能', 'close', 'or', 'open', 'type=creature', 'cr_min=5', 'close')
        self.assertEqual(shape(q.where), '((type:spell ∧ f_school:塑能) ∨ (type:creature ∧ cr_min:5))')
        # An unclosed `(` closes at the end; a stray `)` opens at the start.
        self.assertEqual(shape(line('q=a', 'or', 'open', 'q=b', 'q=c').where), '(q:a ∨ (q:b ∧ q:c))')
        self.assertEqual(shape(line('q=a', 'q=b', 'close', 'or', 'q=c').where), '((q:a ∧ q:b) ∨ q:c)')
        # An empty group stands for nothing, so it neither narrows nor widens.
        self.assertEqual(shape(line('q=a', 'or', 'open', 'close', 'q=b').where), '(q:a ∧ q:b)')
        self.assertEqual(line('open', 'close').where, expression.TRUE)

    def test_a_card_is_a_run_of_one_field_and_each_search_term_stands_alone(self):
        q = line('f_school=a', 'sort=name', 'f_school=b', 'and', 'f_school=c', 'q=x', 'q=x', 'typing=y')
        self.assertEqual([(c.index, c.key, c.values) for c in q.cards()],
                         [(0, 'f_school', ('a', 'b')), (1, 'f_school', ('c',)), (2, 'q', ('x',)), (3, 'q', ('x',)), (4, 'typing', ('y',))])
        self.assertEqual(q.options, {'sort': ['name']})
        # Read as parameters, a field lists the values of all its cards.
        self.assertEqual((q['f_school'], q['q'], q.get('type')), (['a', 'b', 'c'], ['x', 'x'], None))
        # Blank values set nothing and do not split a run.
        self.assertEqual([c.values for c in line('f_school=a', 'f_school=', 'f_school=b', 'q= ').cards()], [('a', 'b')])

    def test_a_link_without_markers_reads_as_before(self):
        q = Query.of({'type': ['spell', 'creature'], 'q': ['火', '球'], 'not_f_school': ['塑能'], 'sort': ['name']})
        self.assertEqual(shape(q.where), '(type:spell,creature ∧ q:火 ∧ q:球 ∧ not_f_school:塑能)')
        self.assertIs(Query.of(q), q)
        for key in ('not_cr_min', 'not_typing', 'not_conflict', 'not_sort'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '无法排除'):
                Query.of({key: ['1']})
        with self.assertRaisesRegex(ValueError, '条件过多'):
            Query.of({'q': [str(i) for i in range(expression.CARD_LIMIT + 1)]})

    def test_removing_a_card_leaves_its_group_and_an_empty_group_leaves_its_own(self):
        q = line('q=a', 'or', 'q=b', 'q=c', 'open', 'q=d', 'close')
        self.assertEqual(shape(expression.without(q.where, 0)), '(q:b ∧ q:c ∧ q:d)')
        self.assertEqual(shape(expression.without(q.where, 2)), '((q:a ∨ q:b) ∧ q:d)')
        self.assertEqual(shape(expression.without(q.where, 3)), '((q:a ∨ q:b) ∧ q:c)')
        self.assertTrue(expression.required(q.where, 2))
        self.assertFalse(expression.required(q.where, 0))

    def test_setting_aside_and_settling_fold_constants(self):
        q = line('f_school=a', 'or', 'q=b', 'type=spell')
        self.assertEqual(shape(expression.relax(q.where, lambda c: c.field == 'f_school')), 'type:spell')
        self.assertEqual(shape(expression.relax(q.where, lambda c: c.field == 'type')), '(f_school:a ∨ q:b)')
        local = q.with_cards(lambda c: Card(c.index, c.key, (expression.NONE,)) if c.key == 'f_school' else c)
        self.assertEqual(shape(expression.settle(local.where)), '(q:b ∧ type:spell)')
        self.assertEqual([c.index for c in local.cards()], [0, 1, 2])
        self.assertEqual(dict(local), {'q': ['b'], 'type': ['spell']})

    def test_the_probe_stands_where_the_caret_is(self):
        q = line('q=a', 'or', 'probe', 'q=b')
        self.assertTrue(q.probing)
        self.assertEqual(shape(q.tree), '((q:a ∨ ?) ∧ q:b)')
        self.assertEqual(shape(q.where), '(q:a ∧ q:b)')
        self.assertEqual([c.index for c in q.cards()], [0, 1])
        self.assertNotEqual(q.key(), line('q=a', 'q=b').key())

    def test_sql_joins_each_cards_own_condition(self):
        q = line('q=a', 'or', 'q=b', 'type=spell')
        text, values = expression.sql(q.where, lambda c: ('x=?', [c.values[0]]))
        self.assertEqual((text, values), ('(((x=?) OR (x=?)) AND (x=?))', ['a', 'b', 'spell']))
        none = Card(0, 'q', (expression.NONE,)), Card(1, 'not_q', (expression.NONE,))
        self.assertEqual([expression.sql(c, None) for c in none], [('0', []), ('1', [])])


if __name__ == '__main__':
    unittest.main()
