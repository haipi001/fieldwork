# Traditional Toolchain Acceptance

Date: 2026-08-28

This matrix separates implementation evidence from external acceptance evidence. A green local check does not claim authorization to scan an external target.

| Requirement | Current evidence | Status |
|---|---|---|
| Capability Registry | Runtime inventory detects executable, version and license; ProjectDiscovery httpx resolves to `/opt/homebrew/bin/httpx` instead of the Python CLI name collision | PASS |
| argv-only execution | `capability_registry.execute` invokes an argv list with `shell=False`, bounded timeout and redacted output | PASS |
| Nuclei / Katana / httpx / Subfinder adapters | Profile-bounded command builders, JSONL parsers and normalized Observation mappings in `traditional_tools.py` | PASS |
| Semgrep / Gitleaks / Trivy adapters | Repository-only command builders, structured parsers, clean-result observations and secret stripping | PASS |
| Planner chain | Subfinder hosts → per-target Scope check → httpx live URLs → per-target Scope check → Katana/Nuclei; code tools run when source exists | PASS |
| Observation → Candidate | Scanner output persists as Observation first; correlation creates Candidate and never Verified | PASS |
| HTTP replay / pentest-ai | Deterministic two-round replay, negative control, proof capsule integrity, safe ptai replay and Verification gate | PASS |
| Local real-tool proof | Product API real-mode fixture executed httpx, Katana and Nuclei; code fixtures executed Semgrep, Gitleaks and Trivy | PASS |
| Pause / Resume / budgets | Persisted run config and unique tool/target checkpoints; request/tool/model/runtime bounds | PASS |
| Scope / network safety | Immutable ScopeSnapshot, DNS/IP/private-network checks, redirect checks, passive-third-party and subdomain opt-ins | PASS |
| Strix CLI adapter | CLI 1.5.3, bounded profile/budget/turns, source snapshot isolation, artifact discovery and sanitized candidate parsing | PASS |
| Strix local prerequisites | Provider and pinned sandbox are independent readiness gates; both fail closed before DNS, budget or launch | PASS |
| Strix sandbox installation | ARM64 manifest cached with Skopeo, imported into Docker, canonical RepoDigest attached, `/bin/sh` smoke test passed | PASS |
| Strix live dry-run | Sandbox is ready; requires local Provider configuration | PENDING |

## Verified installed versions

```text
Strix       1.5.3
Sandbox     1.3.0 / linux-arm64 / sha256:f6906c3114e504fd1a218fcf028d7a0e46851118403a438b63956de6ea7c4331
pentest-ai  1.3.1
Nuclei      3.11.1
Katana      1.7.0
httpx       1.10.0 (ProjectDiscovery, /opt/homebrew/bin/httpx)
Subfinder   2.16.0
Semgrep     1.174.0
Gitleaks    8.30.1
Trivy       0.74.0
```

## Current automated evidence

```text
50 tests passed
Python compile: PASS
JavaScript syntax: PASS
```

External authorized-target traces, real-account tenant tests and human usability sessions remain acceptance evidence, not missing adapter implementation.
