#!/usr/bin/env python3
"""Build a collector artifact without installing or enabling system collection."""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from native_ai_launcher import verify_signature
from version import APP_VERSION


def prepare(output,*,team_id=None,sign_identity=None):
    if platform.system()!='Darwin':raise ValueError('构建需要 macOS SDK')
    if bool(team_id)!=bool(sign_identity):raise ValueError('签名需要同时指定团队与签名身份')
    if team_id and not re.fullmatch(r'[A-Z0-9]{10}',team_id):raise ValueError('签名团队格式无效')
    if sign_identity and (not isinstance(sign_identity,str) or len(sign_identity)>200 or sign_identity=='-'):
        raise ValueError('不能使用临时签名代替发布身份')
    output=Path(output)
    if output.exists():raise ValueError('输出文件已存在，不覆盖现有发布包')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fieldwork-native-build-') as directory:
        binary=Path(directory)/'com.fieldwork.native-ai-collector'
        def run(args):
            result=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,timeout=60,
                env={'PATH':'/usr/bin:/bin','LANG':'C'})
            if result.returncode!=0:raise ValueError('构建、签名或自测失败；未生成发布包')
            return result
        run(['/usr/bin/xcrun','clang','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror',
            str(ROOT/'collectors/native-ai/collector.m'),'-framework','Foundation','-lbsm','-lEndpointSecurity','-o',str(binary)])
        test=run([str(binary),'--self-test'])
        records=[json.loads(line) for line in test.stdout.splitlines()]
        if len(records)!=2 or any(event.get('synthetic') is not True for event in records):
            raise ValueError('构建自测未明确隔离模拟事件')
        if sign_identity:
            run(['/usr/bin/codesign','--sign',sign_identity,'--options','runtime','--timestamp',
                '--identifier','com.fieldwork.native-ai-collector','--entitlements',
                str(ROOT/'collectors/native-ai/entitlements.plist'),str(binary)])
            verify_signature(binary,team_id)
        manifest={'schema':'fieldwork-native-release/1','version':APP_VERSION,
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'signing':'publisher_verified' if sign_identity else 'unsigned_preview',
            'installation_ready':False,'protected_service_included':False,
            'live_collection_verified':False,'synthetic_self_test_passed':True}
        # Exclusive creation prevents a concurrent build from replacing an artifact.
        created=False
        try:
            with output.open('xb') as target:
                created=True
                with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(binary,binary.name)
                    archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                    archive.write(ROOT/'docs/native-ai-collector.md','native-ai-collector.md')
        except Exception:
            # Only remove this build's partially written package, never an existing file.
            if created and output.exists():output.unlink()
            raise
        return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--team-id')
    parser.add_argument('--sign-identity')
    args=parser.parse_args()
    manifest=prepare(args.output,team_id=args.team_id,sign_identity=args.sign_identity)
    print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__':main()
