"""Prepare a fresh owner-private macOS runtime from reviewed, hash-locked wheels."""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'requirements-macos-arm64-py313.lock'
LINE=re.compile(r'([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})\Z')


def install(wheelhouse):
    if platform.system()!='Darwin' or platform.machine()!='arm64' or sys.version_info[:2]!=(3,13):
        raise ValueError('Runtime lock requires macOS arm64 CPython 3.13')
    payload=LOCK.read_bytes();digest=hashlib.sha256(payload).hexdigest()
    expected={}
    for text in payload.decode().splitlines():
        if not text or text.startswith('#'):continue
        match=LINE.fullmatch(text)
        if not match:raise ValueError('Invalid runtime lock line')
        name,version,sha=match.groups()
        if name in expected:raise ValueError('Duplicate runtime lock package')
        expected[name]={'version':version,'sha256':sha}
    if not expected:raise ValueError('Empty runtime lock')
    # Only reviewed hashes enter a private staging wheelhouse. Other input files
    # cannot be chosen by pip, even if they advertise a matching name/version.
    wheelhouse=Path(wheelhouse)
    sources={}
    for file in wheelhouse.glob('*.whl'):
        if file.is_symlink() or not file.is_file():raise ValueError('Wheel must be a regular file')
        sha=hashlib.sha256(file.read_bytes()).hexdigest()
        if sha in {record['sha256'] for record in expected.values()}:
            if sha in sources:raise ValueError('Duplicate reviewed wheel')
            sources[sha]=file
    if set(sources)!={record['sha256'] for record in expected.values()}:
        raise ValueError('Reviewed wheel missing or changed')
    base=Path.home()/'Library/Application Support/Fieldwork/runtimes'
    base.mkdir(parents=True,exist_ok=True,mode=0o700)
    if base.is_symlink() or base.stat().st_uid!=os.getuid():raise ValueError('Runtime root must be owner controlled')
    base.chmod(0o700)
    sys.path.insert(0,str(ROOT))
    from storage_paths import validate_directory
    validate_directory(str(base))
    destination=Path(tempfile.mkdtemp(prefix='py313-'+digest[:12]+'-',dir=base))
    environment=dict(os.environ,PIP_CONFIG_FILE=os.devnull)
    for name in ('PIP_EXTRA_INDEX_URL','PIP_INDEX_URL','PIP_TRUSTED_HOST','PYTHONPATH','PYTHONHOME'):
        environment.pop(name,None)
    try:
        with tempfile.TemporaryDirectory(prefix='locked-wheels-',dir=destination) as directory:
            staging=Path(directory)
            for sha,file in sources.items():
                data=file.read_bytes()
                if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Wheel changed during staging')
                target=staging/file.name;target.write_bytes(data);target.chmod(0o600)
            frozen_lock=staging/'runtime.lock';frozen_lock.write_bytes(payload);frozen_lock.chmod(0o600)
            subprocess.run([sys.executable,'-m','venv',str(destination)],env=environment,check=True)
            python=destination/'bin/python'
            subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--no-index',
                            '--find-links',str(staging),'--require-hashes','-r',str(frozen_lock)],env=environment,check=True)
            subprocess.run([str(python),'-m','pip','check'],env=environment,check=True)
        manifest={'format':'fieldwork-locked-runtime-v1','python':str(python),'lock_sha256':digest,
                  'platform':'macos-arm64-cpython313','packages':expected,'state':'prepared_not_selected',
                  'installed_file_integrity_verified':False,'publisher_signatures_verified':False}
        (destination/'fieldwork-runtime.json').write_text(json.dumps(manifest,indent=2))
        (destination/'fieldwork-runtime.json').chmod(0o600)
        return manifest
    except BaseException:
        # Preserve the failed staging location for diagnosis; never touch an
        # existing selected runtime or the application bundle.
        (destination/'PREPARATION_FAILED').touch(mode=0o600)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('wheelhouse');parser.add_argument('--result',required=True)
    args=parser.parse_args();value=install(args.wheelhouse);Path(args.result).write_text(json.dumps(value,indent=2))
