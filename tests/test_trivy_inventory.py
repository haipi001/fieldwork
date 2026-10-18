import json
import pytest
from traditional_tools import parse_output
from candidate_quality import security_category


def test_trivy_inventory_is_observation_not_security_candidate():
    values=parse_output('trivy',json.dumps({'SchemaVersion':2,'Results':[{'Target':'requirements.txt','Packages':[{
        'Name':'requests','Version':'2.32.5','Identifier':{'PURL':'pkg:pypi/requests@2.32.5'}}]}]}))
    assert len(values)==1
    assert values[0]['observation_type']=='code.dependency_inventory'
    assert values[0]['structured']['InstalledVersion']=='2.32.5'
    assert security_category(values[0]) is None


@pytest.mark.parametrize('packages',[None,{},['bad'],[{'Name':'requests'}],[{'Name':'a','Version':'1','Identifier':{'PURL':'pkg:pypi/a@1'}}]*201])
def test_incomplete_inventory_is_explicit_not_silently_trusted(packages):
    values=parse_output('trivy',json.dumps({'SchemaVersion':2,'Results':[{'Packages':packages}]}))
    assert any(x['observation_type']=='code.dependency_inventory_incomplete' for x in values)
    assert all(security_category(x) is None for x in values)
