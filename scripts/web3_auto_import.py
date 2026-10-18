#!/usr/bin/env python3
"""CLI for the same service used by the Fieldwork App."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web3_contract_import import import_contract, ImportFailure
from storage_paths import data_directory
from storage_access import storage_access


def main():
    parser = argparse.ArgumentParser(description='Fieldwork Web3 合约自动导入（仅公开源码与只读 RPC）')
    parser.add_argument('--address', required=True)
    parser.add_argument('--chain-id', type=int)
    parser.add_argument('--mode', choices=['source', 'build', 'align'], default='build')
    parser.add_argument('--rpc-env', default='ETHEREUM_RPC_URL')
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    try:
        with storage_access(data_directory().resolve()):
            result = import_contract(args.address, args.chain_id, args.mode, os.environ.get(args.rpc_env), args.refresh)
    except (ImportFailure, ValueError, OSError) as error:
        result = {'status': 'blocked', 'error': {'code': getattr(error, 'code', 'local_failure'), 'message': getattr(error, 'message', '检查数据目录或地址配置')}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in {'source_ready', 'compiled', 'aligned', 'ready_for_research'} or (args.mode == 'build' and result['status'] == 'chain_unchecked') else 2


if __name__ == '__main__':
    raise SystemExit(main())
