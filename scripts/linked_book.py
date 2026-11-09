"""Generate a byte-preserving source copy plus DOM range bindings. Never serialize source HTML."""
import argparse
from collections import defaultdict
from contextlib import closing
from hashlib import sha256
import html
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import quote
from html_extract import Document
from build import read_page
from paths import PROJECT, WEB
from review_store import require_current, load_events
from reader import SCHEMA, build_navigation, content_file

BOOK=PROJECT/'data/5echm-anna'
MARKER=b'<!-- anna-link-layer -->'

def fingerprint(path):return sha256(Path(path).read_bytes()).hexdigest()

def pointer(doc,node,offset):
    path=[];n=node
    while n is not doc.body:
        parent=n.parent
        if parent is None:raise ValueError('DOM node outside body')
        path.append(next(i for i,c in enumerate(parent.contents) if c is n));n=parent
    return [list(reversed(path)),len(str(node)[:offset].encode('utf-16-le'))//2]

def text_slice(doc,a,b):
    """Canonical codepoint offsets -> original DOM UTF-16 boundaries, including collapsed whitespace."""
    parts=[]
    for start,end,node in doc.text_nodes:
        if end<=a:continue
        if start>=b:break
        raw=str(node);bounds=[0]
        for m in re.finditer(r'\s+|[^\s]',raw):bounds.append(m.end())
        lo=max(a-start,0);hi=min(b-start,end-start)
        if hi>lo:parts.append((node,bounds[lo],bounds[hi]))
    if not parts:return None
    return {'a':pointer(doc,parts[0][0],parts[0][1]),'b':pointer(doc,parts[-1][0],parts[-1][2]),
            'text':''.join(str(n)[lo:hi] for n,lo,hi in parts)}

def inject(raw,payload,encoding='utf-8'):
    # ASCII-only JSON also works for GB18030 pages. Original bytes remain untouched.
    data=json.dumps(payload,ensure_ascii=True,separators=(',',':')).replace('<','\\u003c')
    addition=MARKER+b'<script type="application/json" id="anna-bindings">'+data.encode('ascii')+b'</script><script charset="utf-8" src="/book/anna-assets/link-labels.js"></script><script charset="utf-8" src="/book/anna-assets/links.js"></script><script charset="utf-8" src="/book/anna-assets/topic.js"></script><!-- /anna-link-layer -->'
    # Append after the document: the HTML parser handles trailing script elements;
    # binding paths refer to the original body before these extra siblings.
    return raw+addition

def build_book(source=WEB,db_path=PROJECT/'data/extracted/catalog.sqlite',output=BOOK):
    source=Path(source).resolve();output=Path(output).resolve();db_path=Path(db_path).resolve()
    if output==source or source in output.parents or output in source.parents:raise ValueError('链接版与原文目录必须分离')
    with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True)) as db:
        summary=json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
        if not summary['full_scan']:raise ValueError('链接版需要完整数据库')
        pages={p:{'sha256':h,'text':t} for p,h,t in db.execute('SELECT path,sha256,text FROM pages')}
        entries={};by_page=defaultdict(list)
        for raw, in db.execute('SELECT data FROM entries'):
            e=json.loads(raw)
            if 'title_end' not in e:raise ValueError('请先重新生成数据库：缺少 title_end')
            entries[e['id']]=e;by_page[e['page_path']].append(e)
        review_snapshot=require_current(db)
        mentions=defaultdict(list)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='concept_observations'").fetchone():
            coverage=json.loads(db.execute("SELECT value FROM metadata WHERE key='concept_coverage'").fetchone()[0])
            for path,expected in coverage['discovery_inputs'].items():
                actual=fingerprint(source/path) if (source/path).exists() else None
                if actual!=expected:raise ValueError('概念发现依据与数据库快照不一致，请运行 make build：'+path)
        for path,raw in db.execute("SELECT page_path,data FROM effective_references WHERE can_link=1 ORDER BY page_path,start,id"):
            mentions[path].append(json.loads(raw))
        reference_coverage=json.loads(db.execute("SELECT value FROM metadata WHERE key='reference_coverage'").fetchone()[0])
        unresolved=[json.loads(raw) for raw, in db.execute("SELECT data FROM effective_references WHERE signal='explicit' AND status IN ('review','unresolved','ambiguous')")]
    inputs={p.relative_to(source).as_posix():fingerprint(p) for p in source.rglob('*') if p.is_file() and '.git' not in p.relative_to(source).parts and not p.is_symlink()}
    actual_pages={p for p in inputs if p.startswith('topics/') and Path(p).suffix.lower() in {'.html','.htm'}}
    if actual_pages!=set(pages) or any(inputs.get(p)!=v['sha256'] for p,v in pages.items()):raise ValueError('原文与数据库快照不一致，请运行 make build')
    report={'definitions':len(entries),'clickable_definitions':0,'linked_references':0,'empty_definitions':[],'unlocatable_definitions':[],'unresolved':[]}
    report['unresolved']=[{'path':r['path'],'href':r['evidence']['href'],'label':r['text'],'reference_id':r['id'],'reason':r['reason']} for r in unresolved]
    report['reference_coverage']=reference_coverage
    manifest={'reader_schema':SCHEMA,'review_snapshot':review_snapshot,'database_sha256':fingerprint(db_path),'source_root':str(source),'inputs':inputs,'entries':{},'outputs':{}}
    reader=build_navigation(source,pages)
    reader['snapshot']=sha256(json.dumps({'inputs':inputs,'database':manifest['database_sha256'],'review':review_snapshot['sha256']},sort_keys=True).encode()).hexdigest()
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.linked-',dir=output.parent) as tmp:
        stage=Path(tmp)
        for path in inputs:
            if not content_file(path):continue
            dest=stage/path;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/path,dest)
        for path in sorted(actual_pages):
            raw,encoding,_=read_page(source/path);doc=Document(path,raw)
            # Editorial/template pages are byte-snapshotted but excluded from extraction.
            # They still belong in the reader; only bound ranges require canonical text.
            if (by_page.get(path) or pages[path]['text']) and doc.text!=pages[path]['text']:
                raise ValueError('解析文本与数据库不一致：'+path)
            definitions=[];references=[]
            page_entries=[]
            for e in sorted(by_page[path],key=lambda e:(e['start'],-e['end'],e['id'])):
                body=text_slice(doc,e['start'],e['end'])
                title=text_slice(doc,e['start'],min(e['title_end'],e['start']+250))
                if not title or not title['text'].strip():
                    first=next(((max(a,e['start']),min(b,e['end'])) for a,b,n in doc.text_nodes if b>e['start'] and a<e['end'] and str(n).strip()),None)
                    if first:title=text_slice(doc,first[0],min(first[1],first[0]+120))
                if body is None:report['empty_definitions'].append(e['id'])
                definitions.append({'id':e['id'],'name':e['name'],'body':body,'title':title})
                locatable=bool((body and body['text'].strip()) or (title and title['text'].strip()))
                if not locatable:report['unlocatable_definitions'].append(e['id'])
                manifest['entries'][e['id']]={'path':path,'fragment':'anna-'+e['id'],'locatable':locatable}
                page_entries.append({'id':e['id'],'name':e['name'],'fragment':'anna-'+e['id'],'locatable':locatable})
                if title and title['text'].strip():report['clickable_definitions']+=1
            for mention in mentions[path]:
                r=text_slice(doc,mention['start'],mention['end'])
                if not r:raise ValueError('有效引用无法绑定原文：'+mention['id'])
                references.append({'id':mention['target_id'],'reference_id':mention['id'],'range':r});report['linked_references']+=1
            reader['pages'][path]={'title':doc.title,'definitions':page_entries}
            # Even pages without extracted entries participate in reader navigation and history.
            (stage/path).write_bytes(inject((source/path).read_bytes(),{'definitions':definitions,'references':references},encoding))
            doc.close()
        assets=stage/'anna-assets';assets.mkdir(exist_ok=True)
        shutil.copyfile(PROJECT/'book/links.js',assets/'links.js')
        shutil.copyfile(PROJECT/'book/topic.js',assets/'topic.js')
        shutil.copyfile(PROJECT/'web/link-labels.js',assets/'link-labels.js')
        (stage/'anna-reader.json').write_text(json.dumps(reader,ensure_ascii=False,separators=(',',':')))
        for p in stage.rglob('*'):
            if p.is_file():manifest['outputs'][p.relative_to(stage).as_posix()]=fingerprint(p)
        (stage/'anna-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False))
        (stage/'anna-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if load_events(review_snapshot['path'])[1]!=review_snapshot['sha256']:
            raise ValueError('审核日志在生成期间变化，请运行 make build')
        # Only replace a known generated directory; leave arbitrary user directories alone.
        if output.exists() and not (output/'anna-manifest.json').is_file():raise ValueError('拒绝覆盖非生成目录：'+str(output))
        previous=output.with_name(output.name+'.previous')
        if previous.exists():raise ValueError('存在上次构建备份，请核对：'+str(previous))
        if output.exists():output.rename(previous)
        try:shutil.move(str(stage),str(output))
        except BaseException:
            if previous.exists():previous.rename(output)
            raise
        if previous.exists():shutil.rmtree(previous)
    print(json.dumps({k:v for k,v in report.items() if k!='unresolved'},ensure_ascii=False))
    return manifest,report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=WEB);p.add_argument('--db',type=Path,default=PROJECT/'data/extracted/catalog.sqlite');p.add_argument('--output',type=Path,default=BOOK)
    a=p.parse_args();build_book(a.source,a.db,a.output)
