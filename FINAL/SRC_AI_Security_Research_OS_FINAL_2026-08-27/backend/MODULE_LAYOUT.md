# Backend Module Layout

```text
backend/
  core/
    engagements/
    scope/
    policy/
    target_resolver/
    planner/
    executor/
    agent_runtime/
    capability_registry/
    tool_registry/
    skills/
    mcp/
    sandbox/
    observations/
    entities/
    artifacts/
    evidence/
    correlation/
    verification/
    findings/
    impact/
    coverage/
    budget/
    checkpoints/
    memory/
    reports/
    events/

  domains/
    traditional_src/
      target_model/
      recon/
      http/
      browser/
      auth/
      api/
      code/
      business_logic/

    web3/
      programs/
      chains/
      contracts/
      compiler/
      protocol_model/
      static/
      invariants/
      fuzz/
      symbolic/
      fork_lab/
      impact/
      eligibility/

  platform_adapters/
    hackerone/
    bugcrowd/
    intigriti/
    immunefi/
    generic_src/
```

## Dependency Rule

`core` 不依赖 `domains/*`。

`domains/*` 可以依赖 core interfaces。

`platform_adapters/*` 只依赖 canonical schemas/report compiler。
