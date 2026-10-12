"""Offline validation against pinned copies of official CycloneDX schemas."""
import argparse
import hashlib
import json
from pathlib import Path
import jsonschema
from referencing import Registry,Resource

ROOT=Path(__file__).resolve().parents[1]


def validate(path):
    directory=ROOT/'tests/schemas/cyclonedx'
    provenance=json.loads((directory/'SOURCES.json').read_text())
    schemas={}
    registry=Registry()
    for name,record in provenance.items():
        payload=(directory/name).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=record['sha256']:raise ValueError('schema_digest_mismatch')
        if not name.endswith('.json'):continue
        schemas[name]=json.loads(payload)
        registry=registry.with_resource('http://cyclonedx.org/schema/'+name,Resource.from_contents(schemas[name]))
    document=json.loads(Path(path).read_text())
    jsonschema.Draft7Validator(schemas['bom-1.6.schema.json'],registry=registry).validate(document)
    references=[document['metadata']['component']['bom-ref']]+[component['bom-ref'] for component in document['components']]
    if len(set(references))!=len(references):raise ValueError('duplicate_bom_reference')
    references=set(references)
    for item in document['dependencies']:
        if item['ref'] not in references or not set(item['dependsOn']).issubset(references):raise ValueError('dangling_dependency_reference')
    return {'schema':'CycloneDX 1.6','validation':'passed','components':len(document['components']),'network_schema_fetch':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('sbom');args=parser.parse_args()
    print(json.dumps(validate(args.sbom)))
