# 06 — Web3 / Immunefi Runtime

## 当前优先：EVM

首版支持：
- Solidity
- Foundry
- Hardhat
- deployed contract + source
- Immunefi program

未来通过 Runtime 扩展 Solana / Move / Cairo。

## Pipeline

```text
Program / Target
→ ProgramSnapshot
→ source/deployment alignment
→ compile
→ ABI/AST/bytecode
→ proxy/implementation
→ protocol graph
→ invariants
→ static
→ fuzz / symbolic
→ local fork
→ state/balance/trace diff
→ impact
→ eligibility
→ report
```

## Protocol Model

节点：
- contract
- proxy
- implementation
- library
- token
- vault
- pool
- oracle
- role
- governance
- bridge
- external dependency

状态转移：
- deposit/withdraw
- mint/burn
- borrow/repay
- liquidate
- swap
- bridge
- upgrade
- governance execute

## Invariants

- authorization
- accounting
- solvency
- oracle
- share/assets
- debt/collateral
- upgradeability
- cross-contract state

## Execution

默认：
- production chain READ ONLY
- public testnet READ ONLY
- local fork read/write
- local devnet read/write
- real private keys not required

## Finding 额外数据

- chain id
- contract address
- fork block
- source commit
- deployed bytecode hash
- invariant violated
- transaction/call trace
- balance diff
- storage diff
- affected funds estimate
- feasibility
- program eligibility
