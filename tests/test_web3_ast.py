import json
import shutil
from pathlib import Path

import pytest

from web3_analysis import run_forge_build
from web3_ast import compiler_analysis
from web3_lab import binary


@pytest.mark.skipif(not binary('forge'), reason='Forge not installed')
def test_real_compiler_resolves_overload_modifier_inheritance_and_external_type(tmp_path):
    (tmp_path / 'foundry.toml').write_text('[profile.default]\nsrc="src"\nout="out"\nsolc_version="0.8.24"\n')
    (tmp_path / 'src').mkdir()
    source = tmp_path / 'src' / 'Vault.sol'
    source.write_text('''pragma solidity ^0.8.24;
interface Token { function transfer(address,uint256) external returns(bool); }
contract Base {
    uint256 internal counter;
    modifier counted() { counter += 1; _; }
    function helper(uint256 x) internal { counter -= x; }
    function helper(address) internal pure { }
}
contract Vault is Base {
    Token token;
    function withdraw(uint256 x) external counted { helper(x); token.transfer(msg.sender,x); }
    function safe(address x) external { helper(x); }
    function unknown(address target) external { target.call(""); }
}
''')
    assert run_forge_build(tmp_path)['status'] == 'compiled'
    result = compiler_analysis(tmp_path)
    assert result['status'] == 'ready'
    unit = result['units'][0]
    entry = next(e for e in unit['entrypoints'] if '.withdraw(' in e['label'])
    assert entry['writes'] == ['src/Vault.sol:Base.counter']
    assert any('.helper(uint256)' in n for n in entry['reachable_functions'])
    assert any('.counted()' in n for n in entry['reachable_functions'])
    assert entry['external_calls'] == ['src/Vault.sol:Token.transfer(address,uint256)']
    assert entry['requires_interaction_review']
    safe = next(e for e in unit['entrypoints'] if '.safe(' in e['label'])
    assert safe['writes'] == []
    assert not safe['requires_interaction_review']
    unknown = next(e for e in unit['entrypoints'] if '.unknown(' in e['label'])
    assert unknown['unresolved'][0]['reason'] == 'dynamic_or_low_level_call'
    assert any(c['bases'] == ['src/Vault.sol:Base'] for c in unit['inheritance'])
    source.write_text(source.read_text() + '\n// changed after build\n')
    stale = compiler_analysis(tmp_path)
    assert stale['status'] == 'unavailable' and stale['rejected_units'] >= 1


def test_missing_corrupt_and_outside_source_units_fail_closed(tmp_path):
    assert compiler_analysis(tmp_path)['status'] == 'unavailable'
    directory = tmp_path / 'out' / 'build-info'
    directory.mkdir(parents=True)
    (directory / 'bad.json').write_text('{bad')
    (directory / 'escape.json').write_text(json.dumps({'input': {'sources': {'../outside.sol': {'content': ''}}}, 'output': {'sources': {'../outside.sol': {'ast': {}}}}}))
    result = compiler_analysis(tmp_path)
    assert result['status'] == 'unavailable' and result['rejected_units'] == 2
