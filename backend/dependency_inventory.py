"""Read installed Python metadata; never import or execute inventoried packages."""
import hashlib
import importlib.metadata as metadata
import json
import uuid
from email.parser import Parser
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
from packaging.requirements import Requirement,InvalidRequirement
from packaging.utils import canonicalize_name
from packaging.version import Version,InvalidVersion
from reporting import redact
from version import APP_VERSION

MAX_PACKAGES=1024


def inventory(requirements=None,distributions=None):
    path=Path(requirements or Path(__file__).resolve().parents[1]/'requirements.txt')
    content=path.read_bytes()
    if len(content)>65536:raise ValueError('requirements_size_limit')
    roots=[]
    for line in content.decode('utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        try:roots.append(Requirement(line))
        except InvalidRequirement as error:raise ValueError('unsupported_requirement_syntax') from error
    available={}
    for count,distribution in enumerate(distributions if distributions is not None else metadata.distributions()):
        if count>=4096:raise ValueError('installed_distribution_limit')
        name=distribution.metadata.get('Name','')
        if not name or len(name)>200:continue
        available.setdefault(canonicalize_name(name),[]).append(distribution)
    root_ref='application:fieldwork'
    queue=[(requirement,root_ref,set(requirement.extras)) for requirement in roots]
    items={};edges={root_ref:set()};problems=[];expanded={};constraints=set()
    while queue:
        if len(items)>MAX_PACKAGES or len(queue)>10000:raise ValueError('dependency_graph_limit')
        requirement,parent,extras=queue.pop(0)
        if requirement.marker and not any(requirement.marker.evaluate({'extra':extra}) for extra in (extras or {''})):continue
        name=canonicalize_name(requirement.name)
        edges.setdefault(parent,set()).add(name);edges.setdefault(name,set())
        matches=available.get(name,[])
        if len(matches)!=1:
            items.setdefault(name,{'name':name,'version':None,'license':None,'state':'missing' if not matches else 'ambiguous','metadata_sha256':None})
            problems.append({'package':name,'kind':items[name]['state']});continue
        distribution=matches[0];raw=distribution.read_text('METADATA') or distribution.read_text('PKG-INFO')
        if raw is None or len(raw.encode())>2*1024*1024:raise ValueError('distribution_metadata_unavailable_or_large')
        parsed=Parser().parsestr(raw)
        if canonicalize_name(parsed.get('Name',''))!=name:raise ValueError('distribution_metadata_changed')
        version=parsed.get('Version')
        if not isinstance(version,str) or len(version)>200:raise ValueError('distribution_version_limit')
        license=parsed.get('License-Expression') or parsed.get('License')
        if not license or license.strip().upper() in {'UNKNOWN','NONE'}:
            license='; '.join(x.split(' :: ')[-1] for x in parsed.get_all('Classifier',[]) if x.startswith('License :: ')) or None
        items[name]={'name':name,'version':version,'license':redact(license[:1000]) if license else None,'state':'installed','metadata_sha256':hashlib.sha256(raw.encode()).hexdigest()}
        check=(name,str(requirement.specifier),bool(requirement.url))
        if check not in constraints:
            constraints.add(check)
            if requirement.url:problems.append({'package':name,'kind':'source_not_verified'})
            try:
                if requirement.specifier and Version(version) not in requirement.specifier:problems.append({'package':name,'kind':'version_mismatch','required':str(requirement.specifier),'installed':version})
            except InvalidVersion:problems.append({'package':name,'kind':'invalid_installed_version'})
        active=expanded.setdefault(name,set())
        requested=extras or {''}
        if requested.issubset(active):continue
        active.update(requested)
        for raw_requirement in parsed.get_all('Requires-Dist',[]):
            try:child=Requirement(raw_requirement)
            except InvalidRequirement as error:raise ValueError('invalid_dependency_metadata') from error
            # Marker extras belong to the parent; requested child extras propagate separately.
            if child.marker and not any(child.marker.evaluate({'extra':extra}) for extra in active):continue
            child.marker=None
            queue.append((child,name,set(child.extras)))
        if len(items)>MAX_PACKAGES or len(queue)>10000:raise ValueError('dependency_graph_limit')
    ordered=sorted(items.values(),key=lambda item:item['name'])
    for item in ordered:
        if item['state']=='installed' and not item['license']:problems.append({'package':item['name'],'kind':'license_not_declared'})
    result={'format':'fieldwork-python-inventory-v1','created_at':datetime.now(timezone.utc).isoformat(),
            'root_ref':root_ref,'requirements_sha256':hashlib.sha256(content).hexdigest(),'items':ordered,
            'dependencies':{name:sorted(values) for name,values in sorted(edges.items())},
            'problems':problems,'scope':'declared_runtime_dependencies_and_installed_transitive_metadata',
            'trust':'unverified_metadata','code_hashes_verified':False,'vulnerabilities_checked':False,
            'excludes':['native_binaries','system_libraries','external_tools','development_dependencies','unrelated_host_packages']}

    result['revision']=hashlib.sha256(json.dumps({key:value for key,value in result.items() if key!='created_at'},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def sbom(value):
    refs={item['name']:'python:'+item['name'] for item in value['items']};refs[value['root_ref']]=value['root_ref']
    components=[]
    for item in value['items']:
        component={'type':'library','bom-ref':refs[item['name']],'name':item['name'],
                   'properties':[{'name':'fieldwork:inventory-state','value':item['state']},{'name':'fieldwork:trust','value':'unverified_metadata'}]}
        if item['version']:
            component.update(version=item['version'],purl='pkg:pypi/'+quote(item['name'],safe='')+'@'+quote(item['version'],safe=''))
        if item['license']:component['licenses']=[{'license':{'name':item['license']}}]
        if item['metadata_sha256']:component['properties'].append({'name':'fieldwork:metadata-text-sha256','value':item['metadata_sha256']})
        components.append(component)
    return {'bomFormat':'CycloneDX','specVersion':'1.6','serialNumber':'urn:uuid:'+str(uuid.uuid4()),'version':1,
            'metadata':{'timestamp':value['created_at'],'component':{'type':'application','bom-ref':value['root_ref'],'name':'Fieldwork','version':APP_VERSION},
                        'properties':[{'name':'fieldwork:scope','value':value['scope']},{'name':'fieldwork:requirements-sha256','value':value['requirements_sha256']},{'name':'fieldwork:limitations','value':json.dumps(value['excludes'])},{'name':'fieldwork:problems','value':json.dumps(value['problems'])}]},
            'components':components,'dependencies':[{'ref':refs[name],'dependsOn':[refs[child] for child in children]} for name,children in value['dependencies'].items()]}
