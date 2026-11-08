"""Structural name parsing, independent of particular publications or item names."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_extract import compact,parse_name,split_name
from concepts import aliases


class NameTests(unittest.TestCase):
    def assert_name(self,raw,zh,en,rule=None):
        parsed=parse_name(raw)
        self.assertEqual(split_name(raw),(zh,en))
        self.assertEqual(parsed['raw'],raw)
        self.assertEqual(parsed['offset_basis'],'raw_name_codepoints')
        if rule:self.assertEqual(parsed['rule'],rule)
        for part in parsed['parts']:
            restored=''.join(raw[a:b] for a,b in part['spans'])
            self.assertEqual(compact(restored),part['text'])
        for group in parsed['groups']:
            self.assertEqual(''.join(raw[a:b] for a,b in group['spans']),group['text'])
            self.assertEqual(''.join(raw[a:b] for a,b in group['delimiter_spans']),group['text'][0]+group['text'][-1])
        return parsed

    def test_wrapped_translation_not_cross_field_parentheses(self):
        for left,right in [('(',')'),('（','）')]:
            for space in ('',' ','\n  '):
                raw=f'示例之剑{space}{left}Example blade{right}'
                result=self.assert_name(raw,'示例之剑','Example blade','wrapped_translation')
                self.assertEqual(result['groups'][0]['role'],'translation_wrapper')
        self.assert_name('冥河之剑 (Acheron blade)','冥河之剑','Acheron blade','wrapped_translation')

    def test_qualifiers_and_aliases_remain_inside_names(self):
        cases=[
            ('吉运之石（幸运石） Stone of Good Luck (Luckstone)','吉运之石（幸运石）','Stone of Good Luck (Luckstone)'),
            ('示例生物（1型） Example Creature (Type 1)','示例生物（1型）','Example Creature (Type 1)'),
            ('示例之剑 (Example blade (Greater))','示例之剑','Example blade (Greater)'),
            ('示例法术（Example Spell（Greater））','示例法术','Example Spell（Greater）'),
            ('剑（变体） Example Blade +1','剑（变体）','Example Blade +1'),
            ('剑 +1 Example Blade','剑','+1 Example Blade'),
            ('地底侏儒 Deep Gnomes (Svirfneblin)','地底侏儒','Deep Gnomes (Svirfneblin)'),
            ('撤步Withdraw (花费：1d6)','撤步','Withdraw (花费：1d6)'),
            ('三面骰 D3','三面骰','D3'),
        ]
        for raw,zh,en in cases:
            with self.subTest(raw=raw):self.assert_name(raw,zh,en)

    def test_quotes_apostrophes_case_and_unicode_are_not_rewritten(self):
        for raw,zh,en in [
            ('示例“引号” "Example Name"','示例“引号”','"Example Name"'),
            ('“兽人”培根“Orc”Bacon','“兽人”培根','“Orc”Bacon'),
            ('示例 (example’s Épée)','示例','example’s Épée'),
            ("示例 Example's Name","示例","Example's Name"),
            ('中文名 English Name','中文名','English Name'),
            ('中文名English Name','中文名','English Name'),
            ('普通中文（限定）','普通中文（限定）',''),
            ('English Name (Alias)','','English Name (Alias)'),
        ]:
            with self.subTest(raw=raw):self.assert_name(raw,zh,en)

    def test_uncertain_structure_retains_the_whole_name(self):
        for raw in ('中文 (English','中文 English)','中文（English)','中文 [English）',
                    '中文 (English) Extra','中文 English 中文','魔法物品表A'):
            with self.subTest(raw=raw):
                result=self.assert_name(raw,raw,'')
                self.assertEqual(result['status'],'review')
        self.assertEqual(parse_name('中文 (English')['rule'],'unbalanced_brackets')
        self.assertEqual(parse_name('')['rule'],'empty_name')

    def test_alternative_bilingual_names_pair_each_translation(self):
        # One row offering alternatives must not hand the last English word to the whole row.
        for raw,zh,en in [
            ('1 磅肉桂 cinnamon 或胡椒 pepper 或 1 头绵羊 sheep','1 磅肉桂或胡椒或 1 头绵羊','cinnamon / pepper / sheep'),
            ('甲饰 Example Ring/乙饰Other Ring','甲饰/乙饰','Example Ring / Other Ring'),
            ('壶 Jug 或罐 Pitcher','壶或罐','Jug / Pitcher'),
        ]:
            with self.subTest(raw=raw):
                result=self.assert_name(raw,zh,en,'bilingual_alternatives')
                self.assertEqual([p['language'] for p in result['parts']],['zh','en']*(len(result['parts'])//2))
                for note in result['annotations']:
                    self.assertEqual(''.join(raw[a:b] for a,b in note['spans']),note['text'])
        # A conjunction inside one Chinese name, a Latin token inside it, or an unpaired segment never splits.
        self.assert_name('微型或小型 Tiny or Small','微型或小型','Tiny or Small','bilingual_suffix')
        self.assert_name('AHA成员 Member of AHA','AHA成员','Member of AHA','bilingual_suffix')
        self.assert_name('附录A：设定汇编 Lore Glossary','附录A：设定汇编','Lore Glossary','bilingual_suffix')
        self.assertEqual(parse_name('壶 Jug 或罐')['rule'],'mixed_name_unresolved')

    def test_adjacent_bilingual_names_split_only_at_english_words(self):
        # A translation running straight into the next Chinese name pairs like a conjunction would.
        for raw in ('甲王Lizard King乙后Lizard Queen','甲王 Lizard King 乙后 Lizard Queen'):
            with self.subTest(raw=raw):
                result=self.assert_name(raw,'甲王/乙后','Lizard King / Lizard Queen','bilingual_alternatives')
                self.assertEqual([p['text'] for p in result['parts']],['甲王','Lizard King','乙后','Lizard Queen'])
        # Initialisms, codes and leading Latin terms belong to the Chinese name they open.
        self.assert_name('AHA成员 Member of AHA','AHA成员','Member of AHA','bilingual_suffix')
        self.assert_name('X射线戒指Ring of X-Ray Vision','X射线戒指','Ring of X-Ray Vision','bilingual_suffix')
        self.assert_name('krig符文之旗BANNER OF THE KRIG RUNE','krig符文之旗','BANNER OF THE KRIG RUNE','bilingual_suffix')
        self.assert_name('召唤DM助手 DM Helper','召唤DM助手','DM Helper','bilingual_suffix')
        # A sentence glossing its terms inline is not a list of names, and its Chinese field cannot hold them.
        for raw in ('是否所有 黑暗Darkness 都阻挡 黑暗视觉Darkvision ？','传言如此。可控制原初者ice primordial克瑞欧Cryonax。'):
            with self.subTest(raw=raw):
                self.assertEqual(self.assert_name(raw,raw,'')['rule'],'mixed_name_unresolved')

    def test_level_labels_qualify_feature_names(self):
        # 3级： says when a class grants the feature; the name itself starts after it.
        for raw,zh,en,level in [('3级：绝望打击 Despairing Strikes','绝望打击','Despairing Strikes',3),
                                ('  10 级:示例能力Example','示例能力','Example',10),('17级：K.O.','','K.O.',17)]:
            with self.subTest(raw=raw):
                result=self.assert_name(raw,zh,en)
                note=next(a for a in result['annotations'] if a['role']=='level_label')
                self.assertEqual(note['value'],level)
                self.assertEqual(''.join(raw[a:b] for a,b in note['spans']),note['text'])
        # A label with nothing after it is the whole heading; level words inside a name stay in it.
        self.assert_name('3级：','3级','')
        self.assert_name('一级额外专长 Bonus Feat','一级额外专长','Bonus Feat')
        # Digits glued to an English word belong to it (2nd-Level), unlike a separated qualifier.
        self.assert_name('2级禁令恩惠 2nd-Level Interdict Boons','2级禁令恩惠','2nd-Level Interdict Boons','bilingual_suffix')
        self.assert_name('一级额外专长1st-Level Bonus Feat','一级额外专长','1st-Level Bonus Feat','bilingual_suffix')

    def test_editorial_annotations_have_original_offsets(self):
        raw='  示例（旧版） (  Example\n Name  ) Legacy '
        result=self.assert_name(raw,'示例','Example Name','wrapped_translation')
        self.assertEqual([r['text'].strip() for r in result['annotations']],['（旧版）','Legacy'])
        self.assert_name('粉碎者 CrusherLegacy','粉碎者','Crusher')
        self.assert_name('示例 Example new!','示例','Example')
        self.assert_name('示例 Renew!','示例','Renew!')

    def test_shared_aliases_use_clean_parts_without_changing_entry_identity(self):
        entry={'id':'original-id','type':'magic_item','name':'示例（Example Blade）',
               'name_zh':'示例','name_en':'Example Blade'}
        self.assertIn('Example Blade',aliases(entry))
        self.assertIn('示例',aliases(entry))
        self.assertEqual(entry['id'],'original-id')
        self.assertEqual(entry['name'],'示例（Example Blade）')

    def test_build_records_name_evidence_without_merging_source_entries(self):
        from build import Build,parse_one
        from paths import WEB
        build=Build(WEB)
        for path in ('topics/荒洲探险家指南/荒洲宝藏/宝藏.html','topics/荒洲探险家指南/荒洲宝藏/荒洲的魔法物品.htm'):
            build.consume(parse_one((str(WEB),path,[],build.registry.context(path))))
        entries=[e for e in build.entries if e['name_zh']=='冥河之剑']
        self.assertEqual(len(entries),2)
        self.assertEqual(len({e['id'] for e in entries}),2)
        self.assertEqual({e['name_en'] for e in entries},{'Acheron blade','Acheron Blade'})
        wrapped=next(e for e in entries if e['name_parse']['rule']=='wrapped_translation')
        self.assertEqual(wrapped['name_parse']['raw'],'冥河之剑 (Acheron blade)')
        self.assertEqual(wrapped['name'],'冥河之剑 (Acheron blade)')

    def test_multilingual_qualifiers_do_not_remove_feature_boundaries(self):
        from html_extract import Document
        doc=Document('topics/职业/示例.htm','<p><b>1级：测试 Example</b><br>说明。<br>'
                     '<b>子能力 Subfeature (花费：1d6)</b><br>能力正文。</p>')
        try:
            names=[c.name for c in doc.candidates() if c.method=='feature_inline_heading']
            self.assertIn('子能力 Subfeature (花费：1d6)',names)
        finally:doc.close()

    def test_uncertain_name_does_not_erase_an_existing_structural_heading(self):
        from html_extract import Document
        raw='<p><font color="#800000"><strong>3级："示例 Examples</strong><strong>"</strong></font>'
        raw+='<br>说明。<br><strong>子能力 Subfeature。</strong>正文。</p>'
        doc=Document('topics/职业/示例.htm',raw)
        try:
            candidates=doc.candidates()
            heading=next(c for c in candidates if c.method=='colored_heading')
            self.assertEqual(heading.name,'3级："示例 Examples"')
            self.assertEqual(parse_name(heading.name)['status'],'review')
            self.assertTrue(any(c.method=='feature_inline_heading' and c.name=='子能力 Subfeature' for c in candidates))
        finally:doc.close()


if __name__=='__main__':unittest.main()
