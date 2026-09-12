# AGENTS_FINAL.md

This file supplements/replaces product-level instructions from earlier packages.

## North Star

Build **one autonomous security research pipeline**, not a collection of scanners.

## Non-negotiable rules

1. Five primary user workspaces only.
2. Traditional SRC and Web3 share one core.
3. Every active action passes Scope/Policy.
4. Scanner result is Observation, never automatically VerifiedFinding.
5. VerifiedFinding must have replayable evidence/oracle.
6. Report logic consumes CanonicalFinding only.
7. Missing report fields must never be hallucinated.
8. Platform adapters are configurable/versioned.
9. Web3 production/public-testnet writes denied by default.
10. v1 exports submission packages; it does not auto-submit third-party bounty forms.
11. Do not require a real private key.
12. Prefer deterministic tools and normalization before LLM reasoning.
13. Do not copy whole open-source repositories merely because they are available.
14. License/security/maintenance review is required for dependencies.
15. Old functionality must be migrated, not silently dropped.

## Coding style

- interfaces before implementations
- typed schemas
- adapters for external tools
- event-driven run state
- idempotent operations
- artifact pointers rather than huge DB blobs
- integration tests for boundaries
- timeouts / resource limits on every external tool
