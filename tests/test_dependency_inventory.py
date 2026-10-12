from email.parser import Parser
import pytest
import dependency_inventory as inventory


class Distribution:
    def __init__(self,name,version='1.0',requires=(),license='MIT'):
        self.raw=f'Name: {name}\nVersion: {version}\n'+(f'License: {license}\n' if license else '')
        self.raw+=''.join('Requires-Dist: '+item+'\n' for item in requires)
        self.metadata=Parser().parsestr(self.raw);self.version=version;self.requires=requires
    def read_text(self,name):return self.raw if name=='METADATA' else None


def read(tmp_path,requirements,distributions):
    path=tmp_path/'requirements.txt';path.write_text(requirements)
    return inventory.inventory(path,distributions)


def test_transitive_extras_markers_cycle_and_unrelated_exclusion(tmp_path):
    result=read(tmp_path,'App[feature]==1.0\n',[
        Distribution('App',requires=['base>=1','optional[leaf]; extra == "feature"','ignored; python_version < "2"']),
        Distribution('base',requires=['App']),Distribution('optional',requires=['leaf; extra == "leaf"']),
        Distribution('leaf'),Distribution('unrelated')])
    assert [item['name'] for item in result['items']]==['app','base','leaf','optional']
    assert result['dependencies']['app']==['base','optional']
    assert result['dependencies']['optional']==['leaf']
    assert result['problems']==[]
    assert result['trust']=='unverified_metadata' and not result['code_hashes_verified']


def test_missing_ambiguous_constraint_and_license_reported(tmp_path):
    result=read(tmp_path,'wrong==2\nmissing\nduplicate\n',[
        Distribution('wrong',license=None),Distribution('duplicate'),Distribution('Duplicate')])
    assert {'missing','ambiguous','version_mismatch','license_not_declared'}.issubset({p['kind'] for p in result['problems']})
    bom=inventory.sbom(result)
    refs={c['bom-ref'] for c in bom['components']}|{bom['metadata']['component']['bom-ref']}
    assert all(d['ref'] in refs and set(d['dependsOn'])<=refs for d in bom['dependencies'])
    assert len(refs)==4


def test_project_package_name_does_not_collide_with_root(tmp_path):
    result=read(tmp_path,'fieldwork\n',[Distribution('fieldwork')]);bom=inventory.sbom(result)
    assert bom['metadata']['component']['bom-ref']!=bom['components'][0]['bom-ref']


def test_source_urls_are_not_exported(tmp_path):
    result=read(tmp_path,'app @ https://user:private-value@example.test/app.whl\n',[Distribution('app')])
    assert any(p['kind']=='source_not_verified' for p in result['problems'])
    assert 'private-value' not in str(result) and 'private-value' not in str(inventory.sbom(result))


def test_missing_graph_is_bounded(tmp_path,monkeypatch):
    monkeypatch.setattr(inventory,'MAX_PACKAGES',1)
    with pytest.raises(ValueError,match='dependency_graph_limit'):read(tmp_path,'a\nb\nc\n',[])


def test_unsupported_requirements_do_not_silently_truncate(tmp_path):
    with pytest.raises(ValueError,match='unsupported_requirement_syntax'):read(tmp_path,'-r another.txt\n',[])
