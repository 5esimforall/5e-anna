#!/usr/bin/env python3
"""只读盘点本地镜像；页数与目录节点数均不等于规则条目数。"""
from collections import Counter, defaultdict
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import csv
import json
import re
import subprocess

from anna.paths import PROJECT, WEB
OUT = PROJECT / 'data'

def clean(s):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', '', s))).strip()

def write_csv(name, rows, fields):
    with (OUT / name).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

class Toc(HTMLParser):
    def __init__(self,web=None):
        super().__init__()
        self.web=web or WEB
        self.divs = []
        self.rows = []
        self.anchor = None
        self.labels = {}
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'span' and self.anchor is not None and re.fullmatch(r'l\d+', a.get('id', '')):
            self.anchor['node_id'] = a['id'][1:]
        if tag == 'div':
            self.divs.append(a.get('id', ''))
        if tag == 'a' and a.get('target') == 'content':
            m = re.search(r'LinkClick\((\d+)', a.get('onclick', ''))
            parents = [x[1:] for x in self.divs if re.fullmatch(r'd\d+', x)]
            href = unquote(a.get('href', ''))
            self.anchor = dict(node_id=m.group(1) if m else '', parent_id=parents[-1] if parents else '', href=href, label='', ancestors=parents)
    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor['label'] += data
    def handle_endtag(self, tag):
        if tag == 'div' and self.divs:
            self.divs.pop()
        if tag == 'a' and self.anchor is not None:
            row = self.anchor
            row['label'] = clean(row['label'])
            self.labels[row['node_id']] = row['label']
            row['toc_path'] = ' / '.join([self.labels.get(p,p) for p in row.pop('ancestors')] + [row['label']])
            row['exists'] = (self.web / urlsplit(row['href']).path).is_file()
            row['link_status'] = 'placeholder' if row['href'].startswith('#') else 'present' if row['exists'] else 'missing'
            self.rows.append(row)
            self.anchor = None

# 来源分桶是物理路径分区，不把文件夹名冒充正式书名。
SPLIT = {'第三方', '模组', 'DNDBeyond', '其他', '被遗忘的国度'}
SPLIT2 = {'其他/新UA', '其他/beyond drops', '其他/异界传送Plane Shift', '其他/穆克', '第三方/鬼魅幽谷', '第三方/瓦尔达的秘密尖塔'}
EXCLUDE = {'速查', '速查 - 副本 - 副本', 'Generator', 'template', 'template2', '空白页模板', '格式转换工具包', '鸣谢'}

def bucket(rel):
    p = rel.parts
    if len(p) == 1 or p[0] in EXCLUDE:
        return None
    n = 2 if p[0] in SPLIT and len(p)>2 else 1
    if '/'.join(p[:2]) in SPLIT2 and len(p)>3:
        n = 3
    return '/'.join(p[:n])

# 仅给出候选领域，须逐页确认，不能作为最终条目标签。
DOMAIN_PATTERNS = {
    '职业/子职/职业选项': r'职业|子职|道途|魔契|祈唤|战技|超魔|灌注|铳士|拳斗士|血猎手|邪狱使|魔战士',
    '种族/物种/血统': r'种族|物种|血统|龙纹',
    '背景': r'背景',
    '专长': r'专长',
    '法术': r'法术',
    '普通装备/工具/载具': r'装备|武器|护甲|工具|载具|船舶|战争机器',
    '魔法物品/奖励': r'魔法物品|宝藏|珍宝|神器|超自然赠礼|祝福|护符',
    '怪物/NPC': r'怪物|生物|图鉴|数据卡|NPC|万兽|纲要',
    '规则/状态/行动': r'术语|状态|战斗|规则|动作|行动|疾病|毒药|陷阱|休整',
    '扩展子系统': r'据点|设施|停工|幕间|制造|制作|虔诚|声望|忠诚|变身|变形|组织|派系|恩惠',
    '设定/冒险索引': r'神祇|神系|位面|地理|地点|世界|冒险|战役|宇宙|城镇',
}

def main():
    OUT.mkdir(exist_ok=True)
    toc = Toc()
    toc.feed((WEB/'webhelpcontents.htm').read_text())
    assert len({r['node_id'] for r in toc.rows}) == len(toc.rows)
    assert all(r['node_id'] and (not r['parent_id'] or r['parent_id'] in toc.labels) for r in toc.rows)
    write_csv('toc.csv', toc.rows, ['node_id','parent_id','label','href','toc_path','exists','link_status'])
    bypath = defaultdict(list)
    for r in toc.rows:
        bypath[urlsplit(r['href']).path].append(r)
    pages = sorted(p for p in (WEB/'topics').rglob('*') if p.suffix.lower() in {'.htm','.html'})
    groups = defaultdict(list)
    excluded = Counter()
    for p in pages:
        b = bucket(p.relative_to(WEB/'topics'))
        if b:
            groups[b].append(p)
        else:
            excluded[p.relative_to(WEB/'topics').parts[0]] += 1
    rows=[]
    for b, files in sorted(groups.items()):
        ns=[n for p in files for n in bypath.get(p.relative_to(WEB).as_posix(),[])]
        matched=defaultdict(list)
        for p in files:
            evidence=p.relative_to(WEB).as_posix()
            hay=evidence+' '+' '.join(n['toc_path'] for n in bypath.get(evidence,[]))
            for domain, pattern in DOMAIN_PATTERNS.items():
                if re.search(pattern,hay):matched[domain].append(evidence)
        origin = ('合作内容（本地归类）' if b.startswith('第三方') else '测试材料（待细分）' if b.startswith('其他/新UA') else '数字发布（出版方待核）' if b.startswith('DNDBeyond') or b.startswith('其他/beyond drops') else '冒险来源（出版方待核）' if b.startswith('模组') else '待核')
        version='待核'
        if b in {'玩家手册2024','城主指南2024','怪物图鉴2025'}:version='5r（核心）'
        elif b in {'玩家手册','城主指南','怪物图鉴'}:version='5e（旧核心）'
        rows.append(dict(source_path='topics/'+b, local_name=b.split('/')[-1], source_group=origin, ruleset_hint=version, html_pages=len(files), toc_pages=len({urlsplit(n['href']).path for n in ns}), toc_nodes=len(ns), candidate_domains='；'.join(matched), domain_evidence='；'.join(d+':'+ps[0] for d,ps in matched.items()), evidence=ns[0]['toc_path'] if ns else '未见目录引用'))
    write_csv('source_paths.csv',rows,['source_path','local_name','source_group','ruleset_hint','html_pages','toc_pages','toc_nodes','candidate_domains','domain_evidence','evidence'])
    # Source mapping now comes from TOC ownership and exact source names.
    from anna.source.registry import SourceRegistry
    registry=SourceRegistry(WEB)
    registry.register_orphans([p.relative_to(WEB).as_posix() for p in pages if bucket(p.relative_to(WEB/'topics'))])
    catalog=[]
    for r in registry.catalog:
        sources=[s for s in registry.sources.values() if r['id'] in s['catalog_ids']]
        local_files={p for s in sources for p in s['paths'] if (WEB/p).is_file()}
        referenced={p for p in local_files if p in bypath}
        catalog.append(dict(section=r['section'],name_zh=r['name_zh'],name_en=r['name_en'],abbreviations=r['abbreviations'],aliases=r['aliases'],
            catalog_level=r['level'],ruleset_hint=r['ruleset_hint'],source_ids='；'.join(s['id'] for s in sources),
            local_paths='；'.join(sorted({s['namespace_hint'] for s in sources if s['namespace_hint']})),html_pages=len(local_files),toc_pages=len(referenced),
            coverage='有目录引用；完整性待核' if referenced else '仅见文件；正文待核' if local_files else '系列分组' if r['level']=='collection' else '仅名录；尚未定位正文',
            evidence='topics/速查/资源简写.htm'))
    write_csv('resource_catalog.csv',catalog,['section','name_zh','name_en','abbreviations','aliases','catalog_level','ruleset_hint','source_ids','local_paths','html_pages','toc_pages','coverage','evidence'])
    stats=dict(snapshot_commit=subprocess.check_output(['git','-C',str(WEB),'rev-parse','HEAD'],text=True).strip(),html_pages=len(pages),toc_nodes=len(toc.rows),toc_unique_local_pages=len({urlsplit(r['href']).path for r in toc.rows if r['href'].startswith('topics/')}),missing_toc_nodes=sum(r['link_status']=='missing' for r in toc.rows),placeholder_toc_nodes=sum(r['link_status']=='placeholder' for r in toc.rows),source_path_buckets=len(rows),resource_catalog_rows=len(catalog),catalog_sections=dict(Counter(x['section'] for x in catalog)),excluded_path_page_counts=dict(excluded))
    (OUT/'inventory.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
