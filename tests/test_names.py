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
