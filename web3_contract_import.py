"""Public-source import. No transactions, remote commands, or authorization promotion."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx

EXPLORERS = {'etherscan.io': 1, 'sepolia.etherscan.io': 11155111, 'arbiscan.io': 42161,
             'optimistic.etherscan.io': 10, 'basescan.org': 8453, 'bscscan.com': 56,
             'polygonscan.com': 137, 'snowtrace.io': 43114}
MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 1000
ADDRESS = re.compile(r'0x[0-9a-fA-F]{40}')
IMPLEMENTATION_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
BEACON_SLOT = '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50'


class ImportFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def parse_target(value: str, chain_id: int | None = None):
    value = value.strip()
    explorer = None
    if not ADDRESS.fullmatch(value):
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.hostname not in EXPLORERS or parsed.username or parsed.password or parsed.port:
            raise ImportFailure('invalid_target', '请输入合约地址或受支持的 HTTPS 合约浏览器地址')
        match = re.fullmatch(r'/address/(0x[0-9a-fA-F]{40})/?', parsed.path)
        if not match:
            raise ImportFailure('invalid_target', '浏览器 URL 必须是 /address/{合约地址}')
        inferred = EXPLORERS[parsed.hostname]
        if chain_id is not None and chain_id != inferred:
            raise ImportFailure('chain_id_mismatch', '所选链与合约浏览器不一致')
        chain_id, value, explorer = inferred, match.group(1), parsed.hostname
    if not isinstance(chain_id, int) or isinstance(chain_id, bool) or chain_id <= 0:
        raise ImportFailure('chain_required', '裸地址需要明确选择 Chain ID')
    address = value.lower()
    explorer = explorer or next((k for k, v in EXPLORERS.items() if v == chain_id), None)
    return {'address': address, 'chain_id': chain_id, 'explorer_url': f'https://{explorer}/address/{address}' if explorer else None, 'parse_status': 'parsed'}


def validate_rpc(value):
    parsed = urlparse(value)
    if parsed.hostname in EXPLORERS or '/address/' in parsed.path:
        raise ImportFailure('explorer_is_not_rpc', '这是浏览器页面，请输入 Ethereum JSON-RPC HTTPS URL')
    local = parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or (parsed.scheme != 'https' and not (local and parsed.scheme == 'http')):
        raise ImportFailure('invalid_rpc', '远程 JSON-RPC 必须使用 HTTPS，本机可使用 HTTP')
    return value


def safe_sources(sources):
    if not isinstance(sources, dict) or not sources or len(sources) > MAX_FILES:
        raise ImportFailure('source_limits', '源码为空或文件数量超限')
    result, total, folded = {}, 0, set()
    for name, entry in sources.items():
        if not isinstance(name, str) or len(name) > 500 or '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
            raise ImportFailure('unsafe_source_path', '源码路径不安全')
        parts = name.split('/')
        if name.startswith('/') or any(p in {'', '.', '..'} for p in parts) or len(parts) > 40:
            raise ImportFailure('unsafe_source_path', '源码路径包含绝对路径或路径穿越')
        if name.casefold() in folded or any(name.startswith(x + '/') or x.startswith(name + '/') for x in result):
            raise ImportFailure('duplicate_source_path', '源码文件路径冲突')
        folded.add(name.casefold())
        content = entry.get('content') if isinstance(entry, dict) else entry
        if not isinstance(content, str):
            raise ImportFailure('missing_dependency', '源码缺少内联文件；请提供精确版本依赖，不自动下载远程脚本')
        total += len(content.encode())
        if total > MAX_BYTES:
            raise ImportFailure('source_limits', '源码体积超限')
        result[name] = {'content': content}
    return result


def fetch_json(url, params=None):
    try:
        with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url, params=params) as response:
                if response.status_code == 429:
                    raise ImportFailure('rate_limited', '源码服务限流，请稍后重试')
                if response.status_code == 404:
                    raise ImportFailure('unverified_source', '源码服务未收录此合约')
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ImportFailure('source_limits', '源码服务响应体积超限')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('object required')
        return payload, digest(raw)
    except (httpx.HTTPError, ValueError) as error:
        raise ImportFailure('provider_unavailable', f'源码服务不可用（{type(error).__name__}），请稍后重试') from None


def sourcify(target):
    url = f"https://sourcify.dev/server/v2/contract/{target['chain_id']}/{target['address']}"
    summary, _ = fetch_json(url)
    if summary.get('runtimeMatch') not in {'match', 'exact_match'}:
        raise ImportFailure('unverified_source', 'Sourcify 没有已验证 Runtime 源码')
    payload, raw_hash = fetch_json(url, {'fields': 'compilation,metadata,sources,abi,proxyResolution'})
    if str(payload.get('chainId')) != str(target['chain_id']) or str(payload.get('address', '')).lower() != target['address']:
        raise ImportFailure('provider_target_mismatch', '源码服务返回的链或地址不一致')
    if payload.get('runtimeMatch') not in {'match', 'exact_match'}:
        raise ImportFailure('unverified_source', 'Sourcify 完整响应未证明 Runtime 源码验证')
    compilation = payload.get('compilation') or {}
    if compilation.get('language') != 'Solidity':
        raise ImportFailure('unsupported_language', '当前自动恢复仅支持 Solidity')
    return {'provider': 'sourcify_v2', 'provider_verified': True, 'provider_match': payload.get('runtimeMatch'),
            'raw_sha256': raw_hash, 'sources': safe_sources(payload.get('sources')),
            'compiler_version': compilation.get('compilerVersion'), 'contract_name': compilation.get('name'),
            'fully_qualified_name': compilation.get('fullyQualifiedName'), 'settings': compilation.get('compilerSettings') or {},
            'metadata': payload.get('metadata'), 'abi': payload.get('abi') or [], 'provider_proxy': payload.get('proxyResolution')}


def parse_etherscan(payload, raw_hash=''):
    result = payload.get('result')
    if payload.get('status') != '1' or not isinstance(result, list) or not result:
        reason = 'rate_limited' if 'rate' in str(result).lower() else 'provider_unavailable'
        raise ImportFailure(reason, 'Etherscan 返回错误或限流，请检查 Key 并稍后重试')
    item = result[0]
    raw = item.get('SourceCode', '')
    if not raw.strip():
        raise ImportFailure('unverified_source', 'Etherscan 上的合约源码未验证')
    decoded = raw
    for _ in range(3):
        if isinstance(decoded, str):
            candidate = decoded.strip()
            if candidate.startswith('{{') and candidate.endswith('}}'):
                candidate = candidate[1:-1]
            try:
                decoded = json.loads(candidate)
            except json.JSONDecodeError:
                break
    settings = {}
    if isinstance(decoded, dict):
        sources = decoded.get('sources', decoded)
        settings = decoded.get('settings') or {}
        if decoded.get('language', 'Solidity') != 'Solidity':
            raise ImportFailure('unsupported_language', '当前自动恢复仅支持 Solidity')
    else:
        name = item.get('ContractName') or 'Contract'
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ImportFailure('invalid_contract_name', '合约名称无效')
        sources = {f'{name}.sol': {'content': raw}}
    if not settings:
        settings = {'optimizer': {'enabled': item.get('OptimizationUsed') == '1', 'runs': int(item.get('Runs') or 200)}}
        if item.get('EVMVersion') not in {None, '', 'Default'}:
            settings['evmVersion'] = item['EVMVersion']
        # Legacy library format cannot be guessed safely.
        if item.get('Library'):
            raise ImportFailure('manual_action_required', '旧式库链接需要人工提供 Standard JSON 编译输入')
    abi = item.get('ABI') or []
    if isinstance(abi, str):
        try:
            abi = json.loads(abi)
        except json.JSONDecodeError:
            abi = []
    return {'provider': 'etherscan_v2', 'provider_verified': True, 'raw_sha256': raw_hash,
            'sources': safe_sources(sources), 'settings': settings, 'compiler_version': item.get('CompilerVersion'),
            'contract_name': item.get('ContractName'), 'abi': abi, 'license': item.get('LicenseType'),
            'metadata': None, 'provider_proxy': {'proxy': item.get('Proxy'), 'implementation': item.get('Implementation')}}


def retrieve(target):
    errors = []
    try:
        return sourcify(target)
    except ImportFailure as error:
        errors.append({'code': error.code, 'message': error.message})
    key = os.environ.get('ETHERSCAN_API_KEY')
    if key:
        try:
            payload, raw_hash = fetch_json('https://api.etherscan.io/v2/api', {'chainid': target['chain_id'], 'module': 'contract', 'action': 'getsourcecode', 'address': target['address'], 'apikey': key})
            value = parse_etherscan(payload, raw_hash)
            value['fallback_notes'] = errors
            return value
        except ImportFailure as error:
            errors.append({'code': error.code, 'message': error.message})
    else:
        errors.append({'code': 'etherscan_key_missing', 'message': '可设置 ETHERSCAN_API_KEY 后重新获取；Sourcify 不需要 Key'})
    raise ImportFailure('unverified_source', '；'.join(e['message'] for e in errors))


def private_directory(path):
    # Reject existing links in every component before resolving/creating.
    path = Path(path).absolute()
    for ancestor in [*reversed(path.parents), path]:
        if ancestor.is_symlink():
            raise ImportFailure('unsafe_workspace', '导入目录不得包含符号链接')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ImportFailure('unsafe_workspace', '导入目录不属于当前用户')
    os.chmod(path, 0o700)
    return path


def write_bytes(path, data):
    import stat
    import tempfile
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ImportFailure('unsafe_workspace', '文件必须是无链接的普通文件')
    fd, name = tempfile.mkstemp(prefix='.fieldwork-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    write_bytes(path, encoded(value))


def materialize(base, target, bundle):
    bundle = copy.deepcopy(bundle)
    bundle['sources'] = safe_sources(bundle['sources'])
    version = str(bundle.get('compiler_version') or '').removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:\+commit\.[a-fA-F0-9]+)?', version):
        raise ImportFailure('compiler_version_unknown', '无法恢复精确 solc 版本')
    settings = copy.deepcopy(bundle['settings'])
    settings.pop('compilationTarget', None)
    # Validate import remappings so they cannot read outside the snapshot.
    for mapping in settings.get('remappings', []):
        if not isinstance(mapping, str) or '=' not in mapping:
            raise ImportFailure('unsafe_remapping', '编译 remapping 格式不正确')
        for part in mapping.split('='):
            if part.startswith('/') or '\\' in part or '..' in part.split('/') or any(ord(c) < 32 for c in part):
                raise ImportFailure('unsafe_remapping', '编译 remapping 指向工作区外')
    fingerprint = digest(encoded(bundle))
    root = private_directory(private_directory(base) / str(target['chain_id']) / target['address'] / fingerprint)
    source_root = private_directory(root / 'sources')
    for name, entry in bundle['sources'].items():
        dest = source_root / name
        private_directory(dest.parent)
        write_bytes(dest, entry['content'].encode())
    standard = {'language': 'Solidity', 'sources': bundle['sources'], 'settings': settings}
    write_json(root / 'standard-input.json', standard)
    manifest = {**target, **{k: v for k, v in bundle.items() if k != 'sources'}, 'fingerprint': fingerprint,
                'queried_at': now(), 'files': {k: {'sha256': digest(v['content'].encode()), 'bytes': len(v['content'].encode())} for k, v in bundle['sources'].items()}}
    write_json(root / 'source-manifest.json', manifest)
    optimizer = settings.get('optimizer', {})
    lines = ['[profile.default]', 'src = "sources"', 'test = "test"', 'script = "script"', 'libs = []', 'out = "out"',
             f'solc_version = {json.dumps(version.split("+")[0])}', 'auto_detect_solc = false', 'offline = true', 'ffi = false',
             f'optimizer = {str(bool(optimizer.get("enabled", False))).lower()}', f'optimizer_runs = {int(optimizer.get("runs", 200))}',
             f'via_ir = {str(bool(settings.get("viaIR", False))).lower()}']
    for original, key in [('evmVersion', 'evm_version')]:
        if original in settings:
            lines.append(f'{key} = {json.dumps(settings[original])}')
    metadata = settings.get('metadata', {})
    for original, key in [('bytecodeHash', 'bytecode_hash'), ('appendCBOR', 'cbor_metadata'), ('useLiteralContent', 'use_literal_content')]:
        if original in metadata:
            lines.append(f'{key} = {json.dumps(metadata[original])}')
    if settings.get('remappings'):
        lines.append('remappings = ' + json.dumps([m.split('=', 1)[0] + '=sources/' + m.split('=', 1)[1] for m in settings['remappings']]))
    libraries = []
    for source, links in settings.get('libraries', {}).items():
        for name, address in links.items():
            if not ADDRESS.fullmatch(address):
                raise ImportFailure('invalid_library', '库地址格式不正确')
            libraries.append(f'sources/{source}:{name}:{address}')
    if libraries:
        lines.append('libraries = ' + json.dumps(libraries))
    def toml_inline(value):
        if isinstance(value, dict):
            return '{ ' + ', '.join(json.dumps(str(k)) + ' = ' + toml_inline(v) for k, v in value.items()) + ' }'
        if isinstance(value, (str, int, bool, list)):
            return json.dumps(value)
        raise ImportFailure('unsupported_settings', '编译设置包含不支持的值，请人工提供工程')
    if optimizer.get('details'):
        lines.append('optimizer_details = ' + toml_inline(optimizer['details']))
    write_bytes(root / 'foundry.toml', ('\n'.join(lines) + '\n').encode())
    return root, manifest


def ensure_compiler(version):
    """Download only checksum-bound official solc binaries, never installation scripts."""
    import sys
    if sys.platform != 'darwin':
        raise ImportFailure('compiler_missing', '当前自动编译器安装仅支持 macOS；请安装精确 solc 版本')
    short = version.removeprefix('v').split('+')[0]
    directory = private_directory(Path.home() / '.solc-select' / 'artifacts' / f'solc-{short}')
    binary = directory / f'solc-{short}'
    if binary.is_file() and not binary.is_symlink():
        return binary
    base = 'https://raw.githubusercontent.com/ethereum/solc-bin/gh-pages/macosx-amd64/'
    listing, _ = fetch_json(base + 'list.json')
    name = listing.get('releases', {}).get(short)
    if not isinstance(name, str) or not re.fullmatch(r'solc-macosx-amd64-v[0-9.]+\+commit\.[a-f0-9]+', name):
        raise ImportFailure('compiler_missing', '官方列表没有此精确编译器版本')
    entry = next((e for e in listing.get('builds', []) if e.get('path') == name), None)
    if not entry or ('+' in version and entry.get('longVersion') != version.removeprefix('v')):
        raise ImportFailure('compiler_version_unknown', '官方 solc commit 与源码声明不一致')
    try:
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', base + name) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 64 * 1024 * 1024:
                        raise ImportFailure('compiler_limits', '编译器下载体积超限')
        if digest(raw) != str(entry.get('sha256', '')).removeprefix('0x'):
            raise ImportFailure('compiler_checksum_mismatch', '官方 solc SHA256 不匹配，拒绝执行')
        temporary = directory / 'download.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        os.replace(temporary, binary)
        os.chmod(binary, 0o700)
        return binary
    except httpx.HTTPError:
        raise ImportFailure('compiler_download_failed', f'官方 solc {version} 下载失败，可离线安装精确版本后重试') from None


def build_project(root):
    from isolated_execution import _trusted_solc, run_isolated
    from web3_lab import binary
    from web3_analysis import run_forge_build
    forge = binary('forge')
    if not forge:
        raise ImportFailure('forge_missing', '未安装 forge，请安装官方 Foundry')
    solc = _trusted_solc(root)
    version = json.loads((root / 'source-manifest.json').read_text())['compiler_version'].removeprefix('v')
    short = version.split('+')[0]
    exact_path = Path.home() / '.solc-select' / 'artifacts' / f'solc-{short}' / f'solc-{short}'
    if not exact_path.is_file():
        solc = ensure_compiler(version)
    else:
        solc = exact_path.resolve()
    import subprocess
    actual = subprocess.run([str(solc), '--version'], capture_output=True, text=True, timeout=5).stdout
    if version not in actual:
        raise ImportFailure('compiler_missing', f'需要精确 solc {version}，禁止替换编译器或 pragma')
    result = run_forge_build(root)
    result['output'] = re.sub(r'/[^\s\"\']*fieldwork-isolated-[^/\s]+/workspace/?', '[isolated-workspace]/', result.get('output', ''))
    result['output'] = result.get('output', '').replace(str(solc), '[trusted-solc]')
    write_json(root / 'compile-log.json', result)
    if result['status'] != 'compiled':
        return result
    standard = json.loads((root / 'standard-input.json').read_text())
    standard['settings']['outputSelection'] = {'*': {'*': ['abi', 'metadata', 'evm.deployedBytecode', 'evm.bytecode'], '': ['ast']}}
    output = run_isolated([str(solc), '--standard-json'], root, timeout=180, output_limit=32 * 1024 * 1024, input_text=json.dumps(standard))
    try:
        compiled = json.loads(output.stdout)
    except ValueError:
        return {'status': 'failed', 'reason': 'standard_json_failed', 'output': output.stderr[-2000:]}
    if output.returncode != 0 or any(e.get('severity') == 'error' for e in compiled.get('errors', [])):
        return {'status': 'failed', 'reason': 'standard_json_failed', 'output': '\n'.join(e.get('formattedMessage', '') for e in compiled.get('errors', []))[-4000:]}
    write_json(root / 'standard-output.json', compiled)
    return {**result, 'standard_json': 'compiled', 'compiler_version': version}


def rpc_call(endpoint, method, params):
    if method not in {'eth_chainId', 'eth_blockNumber', 'eth_getCode', 'eth_getStorageAt', 'eth_call'}:
        raise ImportFailure('rpc_method_forbidden', '只允许只读 JSON-RPC')
    try:
        with httpx.Client(timeout=12, trust_env=False, follow_redirects=False) as client:
            response = client.post(endpoint, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
            response.raise_for_status()
            if len(response.content) > 2 * 1024 * 1024:
                raise ValueError()
            payload = response.json()
            if payload.get('error') or not isinstance(payload.get('result'), str):
                raise ValueError()
            return payload['result']
    except (httpx.HTTPError, ValueError, AttributeError):
        raise ImportFailure('rpc_unavailable', 'JSON-RPC 请求失败或返回非 JSON；检查节点配置（凭据不写入日志）') from None


def hex_code(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*', value):
            raise ValueError()
        return bytes.fromhex(value[2:])
    except ValueError:
        raise ImportFailure('invalid_bytecode', '节点或编译器返回无效 bytecode') from None


def resolve_proxy(target, endpoint, max_depth=4):
    endpoint = validate_rpc(endpoint)
    chain = int(rpc_call(endpoint, 'eth_chainId', []), 16)
    if chain != target['chain_id']:
        raise ImportFailure('chain_id_mismatch', 'RPC 链与目标链不一致，禁止对齐和 Fork')
    block = int(rpc_call(endpoint, 'eth_blockNumber', []), 16)
    tag, address, seen, steps = hex(block), target['address'], set(), []
    for _ in range(max_depth + 1):
        if address in seen:
            raise ImportFailure('proxy_cycle', 'Proxy 实现地址循环，需要人工审查')
        seen.add(address)
        code = hex_code(rpc_call(endpoint, 'eth_getCode', [address, tag]))
        if not code:
            raise ImportFailure('not_contract', '固定区块上没有合约 Runtime Bytecode')
        implementation = None
        pattern = re.fullmatch(r'363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3', code.hex())
        kind = None
        if pattern:
            implementation, kind = '0x' + pattern.group(1), 'eip1167'
        else:
            for slot, slot_kind in [(IMPLEMENTATION_SLOT, 'eip1967'), (BEACON_SLOT, 'beacon')]:
                raw = hex_code(rpc_call(endpoint, 'eth_getStorageAt', [address, slot, tag]))
                if len(raw) != 32:
                    raise ImportFailure('invalid_storage_slot', '标准代理槽位响应必须是 32 字节')
                if any(raw):
                    if any(raw[:12]):
                        raise ImportFailure('manual_review_required', '标准代理槽位的地址编码不正确')
                    implementation, kind = '0x' + raw[-20:].hex(), slot_kind
                    if kind == 'beacon':
                        beacon = implementation
                        raw = hex_code(rpc_call(endpoint, 'eth_call', [{'to': beacon, 'data': '0x5c60da1b'}, tag]))
                        if len(raw) != 32 or any(raw[:12]) or not any(raw[-20:]):
                            raise ImportFailure('manual_review_required', 'Beacon implementation() 返回无效地址')
                        implementation = '0x' + raw[-20:].hex()
                    break
        steps.append({'address': address, 'runtime_sha256': digest(code), 'kind': kind or 'direct', 'implementation': implementation})
        if not implementation:
            # Conservative disassembly: ignore PUSH data when looking for DELEGATECALL.
            pc, delegate = 0, False
            trailer = solidity_metadata(code)
            executable_end = trailer[0] if trailer else len(code)
            while pc < executable_end:
                opcode = code[pc]; pc += 1
                if opcode == 0xf4:
                    delegate = True
                if 0x60 <= opcode <= 0x7f:
                    pc += opcode - 0x5f
            return {'chain_id': chain, 'block_number': block, 'contract_address': target['address'], 'comparison_address': address,
                    'proxy_detected': len(steps) > 1, 'implementation_address': address if len(steps) > 1 else None,
                    'proxy_steps': steps, 'proxy_status': 'unknown_proxy_pattern' if delegate else 'resolved', 'runtime': '0x' + code.hex()}
        address = implementation
    raise ImportFailure('proxy_depth_exceeded', 'Proxy 递归深度超限，需要人工审查')


def solidity_metadata(code):
    """Recognize a complete Solidity CBOR trailer, never infer arbitrary suffixes."""
    if len(code) < 4:
        return None
    size = int.from_bytes(code[-2:], 'big')
    if size < 1 or size + 2 >= len(code):
        return None
    start = len(code) - size - 2
    raw = code[start:-2]
    cursor = 0
    def read():
        nonlocal cursor
        if cursor >= len(raw):
            raise ValueError()
        token = raw[cursor]; cursor += 1
        major, count = token >> 5, token & 31
        if count >= 25:
            raise ValueError()
        if count == 24:
            count = raw[cursor]; cursor += 1
        if major in {2, 3}:
            end = cursor + count
            if end > len(raw):
                raise ValueError()
            value = raw[cursor:end]; cursor = end
            return value.decode('utf-8') if major == 3 else value
        if major == 5:
            value = {}
            for _ in range(count):
                key, item = read(), read()
                if key in value:
                    raise ValueError()
                value[key] = item
            return value
        if major == 7 and count in {20, 21}:
            return count == 21
        raise ValueError()
    try:
        value = read()
        if cursor != len(raw) or not isinstance(value, dict) or not value or not set(value) <= {'ipfs', 'bzzr0', 'bzzr1', 'solc', 'experimental'}:
            return None
        if not isinstance(value.get('solc'), bytes) or len(value['solc']) != 3:
            return None
        for key in {'ipfs', 'bzzr0', 'bzzr1'} & set(value):
            if not isinstance(value[key], bytes) or len(value[key]) != (34 if key == 'ipfs' else 32):
                return None
        return start, size + 2, value
    except (ValueError, IndexError, TypeError, UnicodeDecodeError):
        return None


def match_runtime(local, chain, immutable_references=None, link_references=None):
    """Report compiler-proven immutable differences. Equivalence never auto-promotes alignment."""
    local, chain = hex_code(local), hex_code(chain)
    result = {'status': 'unknown', 'local_runtime_sha256': digest(local), 'chain_runtime_sha256': digest(chain), 'masked_ranges': []}
    if not local or not chain:
        return result
    if local == chain:
        return {**result, 'status': 'exact_match', 'normalized_sha256': digest(local)}
    if len(local) != len(chain):
        return {**result, 'status': 'mismatch'}
    # Linked libraries must have exact addresses from settings. Do not mask arbitrary links.
    a, b, occupied = bytearray(local), bytearray(chain), set()
    for key, ranges in (immutable_references or {}).items():
        immutable_values = set()
        for ref in ranges:
            start, length = ref.get('start'), ref.get('length')
            if not isinstance(start, int) or not isinstance(length, int) or start < 0 or length != 32 or start + length > len(a):
                return result
            positions = set(range(start, start + length))
            if occupied & positions:
                return result
            occupied |= positions
            immutable_values.add(chain[start:start+length])
            if len(immutable_values) > 1:
                return {**result, 'status': 'mismatch', 'reason': 'inconsistent_immutable_values'}
            if a[start:start+length] != b[start:start+length]:
                result['masked_ranges'].append({'start': start, 'length': length, 'basis': f'solc.immutableReferences.{key}', 'local': local[start:start+length].hex(), 'chain': chain[start:start+length].hex()})
            a[start:start+length] = b'\x00' * length
            b[start:start+length] = b'\x00' * length
    lm, cm = solidity_metadata(local), solidity_metadata(chain)
    if lm and cm and lm[:2] == cm[:2] and lm[2].get('solc') == cm[2].get('solc') and lm[2].get('experimental') == cm[2].get('experimental'):
        start, length, _ = lm
        if not occupied & set(range(start, start + length)) and local[start:] != chain[start:]:
            result['masked_ranges'].append({'start': start, 'length': length, 'basis': 'complete_solidity_cbor_trailer_same_compiler', 'local': local[start:].hex(), 'chain': chain[start:].hex()})
            a[start:] = b'\x00' * length
            b[start:] = b'\x00' * length
    result.update(status='explained_equivalence' if a == b else 'mismatch', masked_bytes=sum(x['length'] for x in result['masked_ranges']), normalized_local_sha256=digest(a), normalized_chain_sha256=digest(b))
    if result['status'] == 'explained_equivalence':
        result['policy'] = 'manual_review_required: normalized differences need independent deployment evidence'
    return result


def align_project(root, resolution):
    manifest = json.loads((root / 'source-manifest.json').read_text())
    output = json.loads((root / 'standard-output.json').read_text())
    candidates = []
    for path, contracts in output.get('contracts', {}).items():
        for name, artifact in contracts.items():
            qualified = manifest.get('fully_qualified_name')
            if qualified and qualified != f'{path}:{name}':
                continue
            if not qualified and name != manifest['contract_name']:
                continue
            deployed = artifact.get('evm', {}).get('deployedBytecode', {})
            comparison = match_runtime('0x' + deployed.get('object', ''), resolution['runtime'], deployed.get('immutableReferences'), deployed.get('linkReferences'))
            candidates.append({'contract': name, 'source': path, **comparison})
    selected = candidates[0] if len(candidates) == 1 else {'status': 'unknown', 'reason': 'ambiguous_artifact'}
    # Exact runtime + unknown proxy is still blocked.
    return {'status': 'aligned' if selected['status'] == 'exact_match' and resolution['proxy_status'] == 'resolved' else 'alignment_blocked',
            'match': selected, 'candidates': candidates, **{k: v for k, v in resolution.items() if k != 'runtime'},
            'blockers': [] if selected['status'] == 'exact_match' and resolution['proxy_status'] == 'resolved' else ['manual_review_required']}


def import_contract(value, chain_id=None, mode='build', rpc_url=None, refresh=False, base=None, progress=None, cancelled=None):
    from storage_paths import data_directory
    import fcntl
    start = time.monotonic()
    target = parse_target(value, chain_id)
    base = private_directory(base or data_directory() / 'web3-imports')
    result = {**target, 'status': 'importing_source', 'chain_check': 'skipped', 'started_at': now()}
    def update(status):
        if cancelled and cancelled():
            raise ImportFailure('cancelled', '导入已取消；已下载材料保留')
        result['status'] = status
        result['elapsed_seconds'] = round(time.monotonic() - start, 2)
        if progress:
            progress(copy.deepcopy(result))
    lockfile = base / '.import.lock'
    fd = os.open(lockfile, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if cancelled and cancelled():
                    raise ImportFailure('cancelled', '导入排队已取消')
                if time.monotonic() > deadline:
                    raise ImportFailure('import_busy', '另一个导入正在编译，请稍后重试')
                time.sleep(0.1)
        update('importing_source')
        resolution = None
        source_target = target
        resolution_error = None
        if mode == 'align' and rpc_url:
            try:
                resolution = resolve_proxy(target, rpc_url)
                source_target = {**target, 'address': resolution['comparison_address']}
                result['chain_check'] = 'checked'
                result['proxy'] = {k: v for k, v in resolution.items() if k != 'runtime'}
            except ImportFailure as error:
                resolution_error = {'code': error.code, 'message': error.message}
                result['chain_check'] = 'failed'
                result['chain_error'] = resolution_error
        index = base / f"cache-{source_target['chain_id']}-{source_target['address']}.json"
        bundle = None
        if index.exists() and not refresh:
            if index.is_symlink():
                raise ImportFailure('unsafe_workspace', '缓存不能是符号链接')
            bundle = json.loads(index.read_text())
            result['cache_hit'] = True
        if bundle is None:
            bundle = retrieve(source_target)
            write_json(index, bundle)
        root, manifest = materialize(base, source_target, bundle)
        result.update(source_path=str(root), provider=bundle['provider'], provider_verified=bundle['provider_verified'], source_sha256=bundle['raw_sha256'], fingerprint=manifest['fingerprint'], file_count=len(manifest['files']), contract_name=bundle['contract_name'], compiler_version=bundle['compiler_version'], settings=bundle['settings'])
        update('source_ready')
        if mode == 'source':
            return result
        update('compile_running')
        build_marker = root / 'build-result.json'
        built = json.loads(build_marker.read_text()) if build_marker.exists() and not refresh and not build_marker.is_symlink() else None
        if not built or built.get('status') != 'compiled':
            built = build_project(root)
            write_json(build_marker, built)
        built['output'] = re.sub(r'/(?:Users|private|home)/[^\s\"\']+', '[local-path]', built.get('output', ''))
        result['compile'] = built
        if built.get('status') != 'compiled':
            update('compile_failed')
            return result
        update('compiled')
        if mode == 'align':
            if resolution_error:
                result['error'] = resolution_error
                update('alignment_blocked')
            elif not resolution:
                result['message'] = '源码已编译；请配置 RPC 后执行只读部署对齐'
                update('chain_unchecked')
            else:
                update('alignment_running')
                result['alignment'] = align_project(root, resolution)
                update(result['alignment']['status'])
        else:
            update('chain_unchecked')
        return result
    except ImportFailure as error:
        result.update(error={'code': error.code, 'message': error.message}, status='cancelled' if error.code == 'cancelled' else ('compile_failed' if result.get('status') == 'compile_running' else ('alignment_blocked' if result.get('chain_check') == 'checked' or mode == 'align' else 'unverified_source')))
        return result
    except (OSError, ValueError, RuntimeError) as error:
        result.update(status='compile_failed' if result.get('source_path') else 'unverified_source', error={'code': 'local_failure', 'message': f'本地处理失败（{type(error).__name__}），检查磁盘容量、编译器与目录权限'})
        return result
    finally:
        os.close(fd)
        result['elapsed_seconds'] = round(time.monotonic() - start, 2)
