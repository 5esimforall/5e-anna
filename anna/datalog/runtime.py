#!/usr/bin/env python3
"""Build and query source-bound Soufflé rules. Python transports data, never evaluates game rules."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

from anna.datalog.sources import (DatalogError, bind_sources, read_json, validate_origins,
                             verify_pages, within)
from anna.paths import PROJECT, WEB
from anna.serialization import canonical, digest

SCHEMA = 1
DEFAULT_LIBRARY = PROJECT / 'rules/core'
DEFAULT_OUTPUT = PROJECT / 'data/extracted/datalog'
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')
RULE_ID = re.compile(r'[a-z][a-z0-9_.-]*\Z')
MAX_FACTS = 100_000
MAX_NUMBER = 2**31 - 1


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n', 'utf-8')


def fact_key(relation, values):
    return digest([relation, values])


def schema_text(schema):
    """One schema defines both the wire format and the Datalog declarations."""
    if set(schema) != {'types', 'relations'}:
        raise DatalogError('invalid_schema', 'types and relations are required')
    lines = []
    for name, spec in schema['types'].items():
        if not IDENTIFIER.fullmatch(name) or spec.get('base') not in {'symbol', 'number'}:
            raise DatalogError('invalid_type', name)
        lines.append(f'.type {name} <: {spec["base"]}')
    for name, spec in schema['relations'].items():
        if not IDENTIFIER.fullmatch(name) or not spec.get('fields'):
            raise DatalogError('invalid_relation', name)
        names = set()
        for field, field_type in spec['fields']:
            if not IDENTIFIER.fullmatch(field) or field in names or field_type not in schema['types']:
                raise DatalogError('invalid_relation_field', name)
            names.add(field)
        signature = ', '.join(f'{field}:{kind}' for field, kind in spec['fields'])
        lines.append(f'.decl {name}({signature})')
        if spec.get('input'):
            if spec['input'] not in {'library', 'scenario', 'infrastructure'}:
                raise DatalogError('invalid_relation_input', name)
            lines.append(f'.input {name}(rfc4180=true)')
        if spec.get('output'):
            # Flat primitive outputs use literal TSV. Control characters are
            # excluded by the wire schema, so no output escaping is needed.
            lines.append(f'.output {name}')
    return '\n'.join(lines) + '\n'


def validate_tuple(schema, relation, values, *, input_kind=None):
    if not isinstance(relation, str):
        raise DatalogError('invalid_relation', str(relation))
    spec = schema['relations'].get(relation)
    if not spec or (input_kind and spec.get('input') != input_kind):
        raise DatalogError('invalid_relation', relation)
    if not isinstance(values, list) or len(values) != len(spec['fields']):
        raise DatalogError('invalid_arity', relation)
    if spec.get('editions') and values[0] not in spec['editions']:
        raise DatalogError('unsupported_relation_edition', relation, edition=values[0])
    for index, ((field, kind), value) in enumerate(zip(spec['fields'], values)):
        domain = schema['types'][kind]
        # Soufflé 2.5 provenance prints symbol contents without escaping quotes
        # or backslashes. Reject these at the boundary, not after a partial proof.
        valid = (isinstance(value, str) and 0 < len(value) <= 2048 and
                 not any(ord(c) < 32 or c in {'"', '\\'} for c in value)) if domain['base'] == 'symbol' else (
                     type(value) is int and -MAX_NUMBER <= value <= MAX_NUMBER)
        if not valid or ('values' in domain and value not in domain['values']):
            raise DatalogError('invalid_value', f'{relation}.{field}', index=index)
        constraint = spec.get('bounds', {}).get(field, {})
        if ('min' in constraint and value < constraint['min']) or ('max' in constraint and value > constraint['max']):
            raise DatalogError('invalid_value', f'{relation}.{field}', index=index)


def clause_blocks(path):
    """A small source-map convention around native Datalog, not a new rule language."""
    lines = path.read_text('utf-8').splitlines()
    blocks, current, start, body = [], None, None, []
    for index, line in enumerate(lines, 1):
        match = re.fullmatch(r'// @rule ([a-z][a-z0-9_.-]*)\s*', line)
        if match:
            if current:
                blocks.append((current, start, index - 1, '\n'.join(body).strip()))
            current, start, body = match[1], index, []
        elif current:
            body.append(line)
        elif line.strip() and not line.strip().startswith('//'):
            raise DatalogError('unmarked_clause', str(path), line=index)
    if current:
        blocks.append((current, start, len(lines), '\n'.join(body).strip()))
    return blocks


def instrument(rule_id, text, schema):
    # Markers survive optimizer rewrites because the gate is an input fact. Engine
    # rule numbers and generated line numbers are never used as knowledge IDs.
    code = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('//')).strip()
    scrubbed = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)
    if ('#' in scrubbed or re.search(r'^\s*\.', scrubbed, re.M) or
            scrubbed.count('.') != 1 or not code.endswith('.')):
        raise DatalogError('invalid_clause', rule_id, reason='one native clause per marker')
    match = re.match(r'([A-Za-z][A-Za-z0-9_]*)\(\s*E\s*[,)]', code)
    if not match or match[1] not in schema['relations'] or schema['relations'][match[1]].get('input'):
        raise DatalogError('invalid_clause_head', rule_id)
    if re.search(r'\bRuleEnabled\s*\(', code) or code.count(':-') != 1:
        raise DatalogError('invalid_clause', rule_id, reason='rule gates are compiler-owned')
    for call in re.finditer(r'\b([A-Za-z][A-Za-z0-9_]*)\(\s*([^,()]+)', scrubbed):
        if call[1] in schema['relations'] and call[2].strip() != 'E':
            raise DatalogError('cross_edition_clause', rule_id, relation=call[1])
    head, body = code.split(':-', 1)
    return head + f':- RuleEnabled({json.dumps(rule_id)}, E),\n' + body.lstrip()


def load_library(root):
    root = Path(root).resolve()
    config = read_json(root / 'library.json')
    if config.get('schema') != SCHEMA or config.get('engine') != {'name': 'souffle', 'version': '2.5'}:
        raise DatalogError('unsupported_library', str(root))
    loaded = {key: read_json(within(root, config[key])) for key in ('relations', 'sources', 'rules', 'facts', 'cases', 'scope')}
    schema = loaded['relations']
    program = schema_text(schema)
    required_gate = {'fields': [['rule_id', 'RuleId'], ['edition', 'Edition']], 'input': 'infrastructure', 'key': [0, 1]}
    if schema['relations'].get('RuleEnabled') != required_gate:
        raise DatalogError('invalid_rule_gate', 'RuleEnabled')
    rules, seen = loaded['rules'], set()
    if not isinstance(rules, dict) or not rules:
        raise DatalogError('invalid_rule_registry')
    rule_map = {}
    files = {config[key] for key in loaded} | {'library.json'}
    for module in config['modules']:
        file = within(root, module)
        files.add(module)
        for rid, start, end, code in clause_blocks(file):
            if rid in seen or rid not in rules:
                raise DatalogError('duplicate_or_unknown_rule', rid)
            seen.add(rid)
            meta = rules[rid]
            if meta.get('kind') not in {'source', 'projection'} or meta.get('review_status') not in {'unreviewed', 'reviewed'}:
                raise DatalogError('invalid_rule_metadata', rid)
            if meta['review_status'] == 'reviewed' and not meta.get('review_evidence'):
                raise DatalogError('review_evidence_required', rid)
            editions = meta.get('editions', [])
            if not editions or len(editions) != len(set(editions)) or set(editions) - {'5e', '5r'}:
                raise DatalogError('invalid_rule_editions', rid)
            if meta['kind'] == 'source':
                validate_origins(meta.get('sources'), loaded['sources'], editions, rid)
            elif meta.get('sources') != {e: [] for e in editions} or not meta.get('rationale'):
                raise DatalogError('invalid_projection_metadata', rid)
            generated = instrument(rid, code, schema)
            begin = len(program.splitlines()) + 2
            program += f'\n// @rule {rid}\n{generated}\n'
            rule_map[rid] = {**meta, 'id': rid, 'clause_sha256': digest(code), 'clause': code,
                             'authored': {'path': module, 'lines': [start, end]},
                             'generated': {'lines': [begin, len(program.splitlines())]}}
    if seen != set(rules):
        raise DatalogError('unimplemented_rules', ', '.join(sorted(set(rules) - seen)))
    hashes = {name: sha256(within(root, name).read_bytes()).hexdigest() for name in sorted(files)}
    return {'root': root, 'config': config, **loaded, 'schema': schema, 'program': program,
            'rule_map': rule_map, 'file_hashes': hashes, 'revision': digest(hashes)}


def collect_facts(library, cases):
    schema, rows, origins, keys = library['schema'], defaultdict(dict), defaultdict(list), defaultdict(dict)

    def add(relation, values, origin, input_kind):
        validate_tuple(schema, relation, values, input_kind=input_kind)
        key = fact_key(relation, values)
        indexes = schema['relations'][relation].get('key')
        if indexes is not None:
            identity = canonical([values[i] for i in indexes])
            previous = keys[relation].setdefault(identity, key)
            if previous != key:
                raise DatalogError('conflicting_fact', relation, identity=json.loads(identity))
        rows[relation][key] = values
        if origin not in origins[key]:
            origins[key].append(origin)

    for rid, rule in library['rule_map'].items():
        for edition in rule['editions']:
            add('RuleEnabled', [rid, edition], {'kind': 'rule', 'rule_id': rid, 'edition': edition,
                                               'sources': rule['sources'][edition]}, 'infrastructure')
    claim_ids = set()
    for claim in library['facts']:
        cid, relation, values = claim['id'], claim['relation'], claim['values']
        if cid in claim_ids:
            raise DatalogError('duplicate_claim', cid)
        claim_ids.add(cid)
        if claim.get('review_status') not in {'unreviewed', 'reviewed'}:
            raise DatalogError('invalid_claim_review_status', cid)
        if claim['review_status'] == 'reviewed' and not claim.get('review_evidence'):
            raise DatalogError('review_evidence_required', cid)
        validate_tuple(schema, relation, values, input_kind='library')
        validate_origins({values[0]: claim.get('sources')}, library['sources'], [values[0]], cid)
        add(relation, values, {'kind': 'claim', 'claim_id': cid, 'sources': claim['sources'],
                               'review_status': claim.get('review_status', 'unreviewed')}, 'library')
    case_ids = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str) or
                not RULE_ID.fullmatch(case['id']) or case['id'] in case_ids):
            raise DatalogError('invalid_case_id')
        case_ids.add(case['id'])
        if not isinstance(case.get('facts', {}), dict):
            raise DatalogError('invalid_facts', case['id'])
        if set(case) - {'id', 'description', 'facts', 'expect', 'absent', 'explain'}:
            raise DatalogError('unknown_case_field', case['id'])
        for field in ('expect', 'absent', 'explain'):
            if not isinstance(case.get(field, []), list):
                raise DatalogError('invalid_queries', case['id'])
            for query in case.get(field, []):
                if not isinstance(query, dict) or set(query) != {'relation', 'values'}:
                    raise DatalogError('invalid_query', case['id'])
                validate_tuple(schema, query['relation'], query['values'])
        for relation, values_list in case.get('facts', {}).items():
            if not isinstance(values_list, list):
                raise DatalogError('invalid_facts', relation)
            for index, values in enumerate(values_list):
                add(relation, values, {'kind': 'scenario', 'case_id': case['id'], 'relation': relation,
                                      'row': index, 'sources': [], 'json_pointer': f'/facts/{relation}/{index}'}, 'scenario')
    if sum(map(len, rows.values())) > MAX_FACTS:
        raise DatalogError('fact_limit_exceeded', str(MAX_FACTS))
    return rows, origins


def selected_cases(library, case_file=None, case_id=None):
    cases = read_json(case_file) if case_file else library['cases']
    if not isinstance(cases, dict) or cases.get('schema') != SCHEMA or not isinstance(cases.get('cases'), list):
        raise DatalogError('invalid_cases')
    values = cases['cases']
    if case_id:
        values = [case for case in values if case.get('id') == case_id]
        if len(values) != 1:
            raise DatalogError('case_missing_or_ambiguous', case_id)
    return values


def engine_version(executable):
    try:
        result = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=10)
    except FileNotFoundError as exc:
        raise DatalogError('engine_missing', 'Install Soufflé 2.5; see USAGE.md') from exc
    except subprocess.TimeoutExpired as exc:
        raise DatalogError('engine_timeout') from exc
    match = re.search(r'Version:\s*(\d+\.\d+)(?:\.\d+)?', result.stdout)
    if result.returncode or not match or match[1] != '2.5' or 'Word size: 64 bits' not in result.stdout:
        raise DatalogError('unsupported_engine', result.stdout + result.stderr)
    return {'name': 'souffle', 'version': match[1], 'word_size': 64}


def invoke_engine(executable, directory, *, commands=None):
    command = [executable, '--no-preprocessor', '-F', str(directory / 'facts'), '-D', str(directory / 'results')]
    if commands is not None:
        command += ['-t', 'explain']
    command.append(str(directory / 'program.dl'))
    try:
        result = subprocess.run(command, input=commands, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise DatalogError('engine_timeout') from exc
    if result.returncode:
        raise DatalogError('engine_failed', result.stderr or result.stdout, exit_code=result.returncode)
    return result.stdout, result.stderr


def load_results(directory, schema):
    result = {}
    for relation, spec in schema['relations'].items():
        if not spec.get('output'):
            continue
        path = directory / 'results' / (relation + '.csv')
        if not path.is_file():
            raise DatalogError('missing_result', relation)
        rows = []
        for line in path.read_text().split('\n'):
            if not line:
                continue
            row = line.split('\t')
            try:
                values = [int(v) if schema['types'][kind]['base'] == 'number' else v
                          for v, (_, kind) in zip(row, spec['fields'])]
            except ValueError as exc:
                raise DatalogError('invalid_result', relation) from exc
            if len(row) != len(spec['fields']):
                raise DatalogError('invalid_result', relation)
            validate_tuple(schema, relation, values)
            rows.append(values)
        result[relation] = sorted(rows, key=canonical)
    return result


def check_expectations(cases, results, schema):
    failures, queries = [], {}
    for case in cases:
        for name, should_exist in (('expect', True), ('absent', False)):
            for query in case.get(name, []):
                relation, values = query['relation'], query['values']
                validate_tuple(schema, relation, values)
                if relation not in results:
                    raise DatalogError('query_not_exported', relation)
                present = values in results[relation]
                if present != should_exist:
                    failures.append({'case_id': case['id'], 'relation': relation, 'values': values,
                                     'expected': 'present' if should_exist else 'absent'})
        for query in case.get('explain', []):
            validate_tuple(schema, query['relation'], query['values'])
            if query['values'] not in results.get(query['relation'], []):
                raise DatalogError('explanation_result_missing', case['id'], query=query)
            key = fact_key(query['relation'], query['values'])
            queries.setdefault(key, {**query, 'case_ids': []})['case_ids'].append(case['id'])
    if failures:
        raise DatalogError('acceptance_failed', failures=failures)
    return queries


def query_text(relation, values):
    return relation + '(' + ', '.join(json.dumps(v, ensure_ascii=False) for v in values) + ')'


def parse_atom(text, schema):
    match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_]*)\((.*)\)', text, re.S)
    if not match or match[1] not in schema['relations']:
        return None
    try:
        values = json.loads('[' + match[2] + ']')
        validate_tuple(schema, match[1], values)
        return match[1], values
    except (json.JSONDecodeError, DatalogError):
        return None


def json_documents(text):
    decoder, cursor, out = json.JSONDecoder(), 0, []
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor == len(text):
            break
        try:
            value, cursor = decoder.raw_decode(text, cursor)
        except json.JSONDecodeError as exc:
            raise DatalogError('invalid_provenance', text[cursor:cursor + 300]) from exc
        out.append(value)
    return out


def explain(executable, directory, queries, schema, origins, rule_map):
    if not queries:
        return []
    # Soufflé 2.5's interactive parser does not accept negative numeric query
    # arguments. Ground wrapper clauses use the native parser instead. The
    # wrapper is transport only and is removed from the returned proof.
    with (directory / 'program.dl').open('a', encoding='utf-8') as file:
        file.write('\n.decl AnnaTraceQuery(id:symbol)\n.output AnnaTraceQuery(rfc4180=true)\n')
        for key, query in queries.items():
            file.write(f'AnnaTraceQuery("{key}") :- {query_text(query["relation"], query["values"])}.\n')
    commands = ['format json', 'setdepth 200']
    commands += [f'explain AnnaTraceQuery("{key}")' for key in queries]
    stdout, _ = invoke_engine(executable, directory, commands='\n'.join([*commands, 'quit', '']))
    documents = json_documents(stdout)
    if len(documents) != len(queries):
        raise DatalogError('provenance_count_mismatch')
    result = []
    for (key, query), document in zip(queries.items(), documents):
        sources, rules, inputs = set(), set(), {}

        def annotate(node):
            if not isinstance(node, dict):
                raise DatalogError('invalid_provenance_node')
            if 'subproof' in node or node.get('axiom', '').startswith('subproof '):
                raise DatalogError('provenance_depth_exceeded')
            atom = parse_atom(node.get('axiom', node.get('premises', '')), schema)
            bound = {**node}
            if atom:
                relation, values = atom
                refs = origins.get(fact_key(relation, values), [])
                if 'axiom' in node and schema['relations'][relation].get('input') and not refs:
                    raise DatalogError('unmapped_provenance_leaf', node['axiom'])
                bound.update(relation=relation, values=values)
                if refs:
                    bound['origins'] = refs
                for ref in refs:
                    sources.update(ref.get('sources', []))
                    if ref['kind'] == 'rule':
                        rules.add(ref['rule_id'])
                    if ref['kind'] == 'scenario':
                        inputs[canonical(ref)] = ref
            elif 'axiom' in node:
                # The engine's guard rendering can substitute symbol-table IDs
                # and arbitrary values for existential wildcards. Keep it as a
                # diagnostic, never present it as a grounded evidence fact.
                bound['kind'] = 'engine_condition'
                bound['condition_kind'] = 'negation' if node['axiom'].startswith('!') else 'constraint'
            if 'children' in node:
                bound['children'] = [annotate(child) for child in node['children']]
            return bound

        wrapper = document.get('proof', {})
        children = wrapper.get('children', [])
        if len(children) != 1:
            raise DatalogError('invalid_query_wrapper', key)
        proof = annotate(children[0])
        if proof.get('relation') != query['relation'] or proof.get('values') != query['values']:
            raise DatalogError('provenance_query_mismatch')
        relevant_rules = [rule for rule in document.get('rules', [])
                          if any(f'RuleEnabled("{rid}",' in rule.get('rule', '') for rid in rules)]
        result.append({'id': key, **query, 'proof': proof, 'engine_rules': relevant_rules,
                       'source_ids': sorted(sources), 'rule_ids': sorted(rules),
                       'input_origins': list(inputs.values()),
                       'review_statuses': sorted({rule_map[r]['review_status'] for r in rules})})
    return result


@contextmanager
def output_directory(output, protected=()):
    output = Path(output).resolve()
    if any(output == Path(p).resolve() or Path(p).resolve().is_relative_to(output) or
           output.is_relative_to(Path(p).resolve()) for p in protected):
        raise DatalogError('unsafe_output', str(output))
    if output.exists() and not (output / 'manifest.json').is_file():
        raise DatalogError('unowned_output', str(output))
    if output.exists() and read_json(output / 'manifest.json').get('producer') != 'anna-datalog':
        raise DatalogError('unowned_output', str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.datalog-', dir=output.parent) as temp:
        stage = Path(temp) / 'bundle'
        stage.mkdir()
        yield stage
        backup = output.with_name('.' + output.name + '-old-' + uuid4().hex)
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except BaseException:
            if backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def build(library_root=DEFAULT_LIBRARY, db_path=PROJECT / 'data/extracted/catalog.sqlite', source_root=WEB,
          output=DEFAULT_OUTPUT, executable='souffle', base_url='http://127.0.0.1:8000',
          case_file=None, case_id=None, lang='zh'):
    parsed = urlsplit(base_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise DatalogError('invalid_base_url', base_url)
    engine = engine_version(executable)
    library = load_library(library_root)
    cases = selected_cases(library, case_file, case_id)
    rows, origins = collect_facts(library, cases)
    sources, pages = bind_sources(library['sources'], db_path, source_root, base_url)
    inputs = {'library_revision': library['revision'], 'sources': sources, 'cases': cases}
    snapshot = digest(inputs)
    with output_directory(output, (library_root, source_root, db_path)) as directory:
        (directory / 'facts').mkdir()
        (directory / 'results').mkdir()
        (directory / 'program.dl').write_text(library['program'], 'utf-8')
        for relation, spec in library['schema']['relations'].items():
            if spec.get('input'):
                with (directory / 'facts' / (relation + '.facts')).open('w', newline='') as file:
                    writer = csv.writer(file, lineterminator='\n')
                    writer.writerows(sorted(rows.get(relation, {}).values(), key=canonical))
        _, diagnostics = invoke_engine(executable, directory)
        results = load_results(directory, library['schema'])
        violations = results.get('Violation', [])
        if violations:
            raise DatalogError('invalid_scenario', violations=violations)
        queries = check_expectations(cases, results, library['schema'])
        explanations = explain(executable, directory, queries, library['schema'], origins, library['rule_map'])
        source_map = {'schema': SCHEMA, 'snapshot': snapshot, 'sources': sources,
                      'rules': library['rule_map'], 'fact_origins': dict(origins)}
        write_json(directory / 'source-map.json', source_map)
        write_json(directory / 'relations.json', library['schema'])
        write_json(directory / 'cases.json', {'schema': SCHEMA, 'cases': cases})
        write_json(directory / 'results.json', {'schema': SCHEMA, 'snapshot': snapshot, 'relations': results})
        write_json(directory / 'explanations.json', {'schema': SCHEMA, 'snapshot': snapshot, 'explanations': explanations})
        write_json(directory / 'scope.json', library['scope'])
        (directory / 'engine-diagnostics.txt').write_text(diagnostics, 'utf-8')
        manifest = {'schema': SCHEMA, 'producer': 'anna-datalog', 'snapshot': snapshot, 'engine': engine,
                    'library_id': library['config']['id'], 'library_revision': library['revision'],
                    'library_files': library['file_hashes'], 'source_pages': pages,
                    'source_commits': sorted({c for s in sources.values() for c in s['source_commits']}),
                    'counts': {'rules': len(library['rule_map']), 'claims': len(library['facts']),
                               'source_clauses': sum(r['kind'] == 'source' for r in library['rule_map'].values()),
                               'projection_clauses': sum(r['kind'] == 'projection' for r in library['rule_map'].values()),
                               'sources': len(sources), 'pages': len(pages), 'cases': len(cases),
                               'input_facts': sum(map(len, rows.values())),
                               'output_facts': sum(map(len, results.values())), 'explanations': len(explanations)},
                    'acceptance': {'status': 'passed', 'present': sum(len(c.get('expect', [])) for c in cases),
                                   'absent': sum(len(c.get('absent', [])) for c in cases)},
                    'review_status': 'unreviewed' if any(r['review_status'] != 'reviewed'
                        for r in [*library['rule_map'].values(), *library['facts']]) else 'reviewed'}
        from anna.datalog.report import render_report
        labels = read_json(Path(__file__).with_name('labels.json'))
        (directory / 'report.html').write_text(render_report(manifest, source_map, results, explanations,
                                                            library['scope'], labels[lang], lang), 'utf-8')
        manifest['artifacts'] = {str(p.relative_to(directory)): sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(directory.rglob('*')) if p.is_file()}
        write_json(directory / 'manifest.json', manifest)
        verify_pages(pages, source_root)
        for file, expected in library['file_hashes'].items():
            if sha256(within(library_root, file).read_bytes()).hexdigest() != expected:
                raise DatalogError('library_changed_during_build', file)
    return manifest


def verify_bundle(directory):
    directory = Path(directory).resolve()
    manifest = read_json(directory / 'manifest.json')
    if manifest.get('producer') != 'anna-datalog' or manifest.get('schema') != SCHEMA:
        raise DatalogError('unsupported_bundle')
    for file, expected in manifest['artifacts'].items():
        path = within(directory, file)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
            raise DatalogError('artifact_mismatch', file)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    compile_parser = commands.add_parser('build', help='Bind sources, execute core rules, check cases and produce a trace report')
    compile_parser.add_argument('--library', type=Path, default=DEFAULT_LIBRARY)
    compile_parser.add_argument('--db', type=Path, default=PROJECT / 'data/extracted/catalog.sqlite')
    compile_parser.add_argument('--source', type=Path, default=WEB)
    compile_parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    compile_parser.add_argument('--engine', default=os.environ.get('SOUFFLE', 'souffle'))
    compile_parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    compile_parser.add_argument('--cases', type=Path)
    compile_parser.add_argument('--case')
    compile_parser.add_argument('--lang', choices=('zh', 'en'), default='zh')
    inspect = commands.add_parser('inspect', help='Read a checked source map or saved explanation, without re-evaluating')
    inspect.add_argument('--bundle', type=Path, default=DEFAULT_OUTPUT)
    inspect.add_argument('--rule')
    inspect.add_argument('--source-id')
    inspect.add_argument('--explanation')
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build(args.library, args.db, args.source, args.output, args.engine, args.base_url,
                           args.cases, args.case, args.lang)
        else:
            result = verify_bundle(args.bundle)
            selectors = sum(bool(v) for v in (args.rule, args.source_id, args.explanation))
            if selectors > 1:
                raise DatalogError('conflicting_query')
            mapping = read_json(args.bundle / 'source-map.json')
            if args.rule:
                result = mapping['rules'].get(args.rule)
            elif args.source_id:
                source = mapping['sources'].get(args.source_id)
                result = {'source': source, 'rule_ids': [rid for rid, rule in mapping['rules'].items()
                          if any(args.source_id in refs for refs in rule['sources'].values())],
                          'claim_ids': sorted({origin['claim_id'] for origins in mapping['fact_origins'].values()
                              for origin in origins if origin['kind'] == 'claim' and args.source_id in origin['sources']}),
                          'explanation_ids': [proof['id'] for proof in read_json(args.bundle / 'explanations.json')['explanations']
                                              if args.source_id in proof['source_ids']]} if source else None
            elif args.explanation:
                result = next((e for e in read_json(args.bundle / 'explanations.json')['explanations'] if e['id'] == args.explanation), None)
            if result is None:
                raise DatalogError('not_found')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (DatalogError, OSError) as exc:
        error = exc.as_dict() if isinstance(exc, DatalogError) else {'code': 'io_error', 'detail': str(exc)}
        print(json.dumps({'error': error}, ensure_ascii=False), file=__import__('sys').stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
