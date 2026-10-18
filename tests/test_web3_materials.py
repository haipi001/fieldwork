from pathlib import Path
import pytest
from web3_contract_import import ImportFailure, materialize, parse_target
from web3_materials import verified_sources
from tests.test_web3_auto_import import bundle, ADDR


def fixture(tmp_path):
    root,manifest=materialize(tmp_path.resolve()/'imports',parse_target(ADDR,1),bundle())
    return root,manifest


def test_verified_bytes_are_returned_without_unlisted_sources(tmp_path):
    root,manifest=fixture(tmp_path)
    (root/'sources/Extra.sol').write_text('contract Extra {}')
    sources=verified_sources(root,manifest['fingerprint'])
    assert set(sources)==set(manifest['files'])
    assert 'Extra.sol' not in sources


@pytest.mark.parametrize('change',['edit','missing','link','manifest','standard'])
def test_changed_materials_are_rejected(tmp_path,change):
    root,manifest=fixture(tmp_path)
    source=root/'sources'/next(iter(manifest['files']))
    if change=='edit':source.write_text('contract Modified {}')
    elif change=='missing':source.unlink()
    elif change=='link':
        other=tmp_path.resolve()/'other';other.write_bytes(source.read_bytes());source.unlink();source.symlink_to(other)
    elif change=='manifest':(root/'source-manifest.json').write_text('{}')
    else:(root/'standard-input.json').write_text('{}')
    with pytest.raises((ValueError, OSError, ImportFailure)):verified_sources(root,manifest['fingerprint'])
