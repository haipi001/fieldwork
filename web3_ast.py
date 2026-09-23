"""Source-bound Solidity compiler call graph; never a vulnerability oracle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def _storage_root(expression, states):
    if not isinstance(expression, dict):
        return set()
    if expression.get('nodeType') == 'Identifier':
        ref = expression.get('referencedDeclaration')
        return {ref} if ref in states else set()
    for key in ('baseExpression', 'expression'):
        if key in expression:
            return _storage_root(expression[key], states)
    if expression.get('nodeType') == 'TupleExpression':
        return set().union(*(_storage_root(x, states) for x in expression.get('components', [])))
    return set()


def analyze_output(output):
    """IDs are scoped to one compilation unit; do not merge units by numeric ID."""
    nodes, locations, owners, contracts = {}, {}, {}, []
    for source, result in output.get('sources', {}).items():
        ast = result.get('ast', {})
        for node in walk(ast):
            if isinstance(node.get('id'), int):
                nodes[node['id']] = node
                locations[node['id']] = source
        for contract in ast.get('nodes', []):
            if contract.get('nodeType') != 'ContractDefinition':
                continue
            contracts.append(contract)
            for node in walk(contract):
                if 'id' in node:
                    owners[node['id']] = contract['id']
    states = {i for i, n in nodes.items() if n.get('nodeType') == 'VariableDeclaration' and n.get('stateVariable')}
    functions = {i: n for i, n in nodes.items() if n.get('nodeType') in {'FunctionDefinition', 'ModifierDefinition'}}

    def label(i):
        n = nodes.get(i, {})
        owner = nodes.get(owners.get(i), {}).get('name', '')
        params = ','.join(p.get('typeDescriptions', {}).get('typeString', '?') for p in n.get('parameters', {}).get('parameters', []))
        name = n.get('name') or n.get('kind', str(i))
        return f"{locations.get(i, '?')}:{owner}.{name}" + (f'({params})' if i in functions else '')

    records, edges = {}, []
    for fid, function in functions.items():
        reads, writes, calls, unresolved = set(), set(), set(), []
        for node in walk(function.get('body')):
            ref = node.get('referencedDeclaration')
            if ref in states:
                reads.add(ref)
            if node.get('nodeType') == 'Assignment':
                writes.update(_storage_root(node.get('leftHandSide'), states))
            if node.get('nodeType') == 'UnaryOperation' and node.get('operator') in {'++', '--', 'delete'}:
                writes.update(_storage_root(node.get('subExpression'), states))
            if node.get('nodeType') != 'FunctionCall' or node.get('kind') != 'functionCall':
                continue
            expression = node.get('expression', {})
            if expression.get('nodeType') == 'FunctionCallOptions':
                expression = expression.get('expression', {})
            target = expression.get('referencedDeclaration')
            type_string = expression.get('typeDescriptions', {}).get('typeString', '')
            if target in functions:
                external = ' external' in type_string
                target_node = functions[target]
                dynamic = target_node.get('virtual', False) and expression.get('nodeType') == 'Identifier'
                kind = 'typed_external' if external else 'virtual_unresolved' if dynamic else 'internal'
                edges.append({'source': fid, 'target': target, 'kind': kind, 'src': node.get('src')})
                if kind == 'internal' and target_node.get('body') is not None:
                    calls.add(target)
                elif kind != 'typed_external':
                    unresolved.append({'reason': kind if dynamic else 'body_unavailable', 'target': label(target)})
            elif expression.get('nodeType') == 'MemberAccess' or (target is not None and target >= 0):
                unresolved.append({'reason': 'dynamic_or_low_level_call', 'target': expression.get('memberName') or expression.get('name', '?')})
        for modifier in function.get('modifiers', []):
            target = modifier.get('modifierName', {}).get('referencedDeclaration')
            if target in functions and not functions[target].get('virtual'):
                edges.append({'source': fid, 'target': target, 'kind': 'modifier', 'src': modifier.get('src')})
                calls.add(target)
            else:
                unresolved.append({'reason': 'modifier_unresolved', 'target': str(target)})
        records[fid] = {'id': fid, 'label': label(fid), 'visibility': function.get('visibility'),
                        'kind': function.get('kind', 'modifier'), 'reads': reads, 'writes': writes,
                        'calls': calls, 'unresolved': unresolved}
    entries = []
    for fid, record in records.items():
        source_parts = Path(locations[fid]).parts
        if record['visibility'] not in {'external', 'public'} or record['kind'] == 'constructor' or functions[fid].get('body') is None or any(p in {'test', 'tests', 'script', 'scripts'} for p in source_parts):
            continue
        seen, pending = set(), [fid]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(records[current]['calls'] - seen)
        writes = set().union(*(records[i]['writes'] for i in seen))
        reads = set().union(*(records[i]['reads'] for i in seen))
        external = [e for e in edges if e['source'] in seen and e['kind'] == 'typed_external']
        entries.append({'id': fid, 'label': record['label'], 'reads': sorted(label(i) for i in reads),
                        'writes': sorted(label(i) for i in writes), 'reachable_functions': sorted(label(i) for i in seen - {fid}),
                        'external_calls': sorted({label(e['target']) for e in external}),
                        'unresolved': [u for i in sorted(seen) for u in records[i]['unresolved']],
                        'requires_interaction_review': bool(writes and (external or any(records[i]['unresolved'] for i in seen)))})
    return {'entrypoints': entries,
            'functions': [{'id': i, 'label': record['label']} for i, record in records.items()],
            'call_edges': edges,
            'inheritance': [{'contract': f"{locations[c['id']]}:{c['name']}",
                             'bases': [f"{locations.get(i, '?')}:{nodes.get(i, {}).get('name', '?')}" for i in c.get('linearizedBaseContracts', [])[1:]]} for c in contracts]}


def compiler_analysis(root: Path):
    root = root.resolve()
    units, rejected = [], 0
    directory = root / 'out' / 'build-info'
    for path in sorted(directory.glob('*.json')) if directory.is_dir() else []:
        try:
            if path.stat().st_size > 50_000_000:
                rejected += 1
                continue
            raw = path.read_bytes()
            data = json.loads(raw)
            sources = data.get('input', {}).get('sources', {})
            output = data.get('output', {})
            if not sources or not output.get('sources'):
                continue
            # A cached AST is useful only when every compiler input still matches.
            for name, source in sources.items():
                resolved = (root / name).resolve()
                if not resolved.is_relative_to(root) or not isinstance(source.get('content'), str) or resolved.read_bytes() != source['content'].encode():
                    raise ValueError('source_mismatch')
            if any(name not in sources or not result.get('ast') for name, result in output['sources'].items()):
                raise ValueError('incomplete_ast')
            unit = analyze_output(output)
            unit.update({'id': hashlib.sha256(raw).hexdigest(), 'compiler': data.get('solcLongVersion'),
                         'source_hashes': {name: hashlib.sha256(s['content'].encode()).hexdigest() for name, s in sources.items()}})
            units.append(unit)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            rejected += 1
    # Preserve units separately: declaration IDs are not globally unique.
    return {'status': 'ready' if units else 'unavailable', 'units': units, 'rejected_units': rejected,
            'boundary': '编译器声明级调用关系；外部调用仅确定静态类型，部署目标、虚分派、存储别名和经济影响仍需独立验证。'}
