"""Recognize references whose kind is declared by a printed notation or table column."""
from collections import defaultdict
import re

from anna.catalog.reference_slots import resolve_members
from anna.source.html import compact, digest


def damage_notation(vocabulary):
    names = sorted((name for family,name in vocabulary.members if family=='damage'
                    and re.fullmatch(r'[\u3400-\u9fff]+',name)),key=lambda n:(-len(n),n))
    if not names:
        return None
    return re.compile(r'(?<![\da-zA-Z])\d+[ \t]*[dD][ \t]*\d+(?:[ \t]*[+＋−-][ \t]*\d+)?'
                      r'[ \t]*[)）]?[ \t]*(?:点[ \t]*)?('+'|'.join(map(re.escape,names))+r')[ \t]*伤害')


def damage_mentions(text,path,resolver,vocabulary,pattern,non_prose):
    if not pattern:
        return
    for use in pattern.finditer(text):
        a,b = use.span(1)
        scope = resolver.plain_scope(path,*use.span())
        members = vocabulary.candidates('damage',use[1])
        result,proof = resolve_members(resolver,path,a,use[1],scope,members)
        if non_prose(*use.span()):
            result = {**result,'status':'review','reason':'plain_non_prose'}
        elif result['status']=='resolved' and result['targets'][0] in scope['owner_ids']:
            result = {**result,'status':'review','reason':'definition_self_reference'}
        yield (a,b), ('plain',{**proof,'family':'damage','syntax':{'rule':'damage_notation','slot':'damage_type',
                             'cue_span':list(use.span()),'notation':use[0]}},result)


def column_mentions(text,path,tables,resolver,vocabulary):
    for table in tables:
        headers = table['headers']
        if (table.get('irregular_columns') or not {'法术','学派'}<=set(headers) or len(set(headers))!=len(headers)
                or any(row!=headers for row in table.get('repeated_headers',{}).values())):
            continue
        start,end = table['span']
        legends = defaultdict(lambda:defaultdict(dict))
        for column in headers:
            for entry in vocabulary.legends.get((path,column),()):
                d = entry['slot_definition']
                lo,hi = d['scope_span']
                if lo<=start<end<=hi:
                    legends[column][d['symbol']][entry['id']] = [{'member_id':entry['id'],'member_name':entry['name'],
                        'heading':d['heading'],'via':'legend','meaning':d['meaning']}]
        rows = defaultdict(list)
        for row,col,a,b in table['cells']:
            rows[row].append((col,a,b))
        for row,cells in rows.items():
            if len(cells)!=len(headers):
                continue
            for col,a,b in cells:
                header = headers[col]
                value = text[a:b]
                if header=='学派' and re.fullmatch(r'\s*[\u3400-\u9fff]+\s*',value):
                    family = 'school'
                    m = re.search(r'\S+',value)
                    uses = [(m.start(),m.end(),vocabulary.candidates(family,m[0]))]
                elif header in legends and re.fullmatch(r'\s*[A-Z](?:\s*[,，、]\s*[A-Z])*\s*',value):
                    family = 'legend'
                    uses = [(m.start(),m.end(),legends[header].get(m[0],{})) for m in re.finditer('[A-Z]',value)]
                else:
                    continue
                table_ev = {'id':'tbl_'+digest(path,table['index']),'span':table['span'],'row':row,'column':col,
                            'headers':headers,'cell_span':[a,b],'cell_text':compact(value)}
                for lo,hi,members in uses:
                    result,proof = resolve_members(resolver,path,a+lo,value[lo:hi],resolver.plain_scope(path,a,b),members)
                    yield (a+lo,a+hi), ('table',{**proof,'family':family,'table':table_ev},result)
