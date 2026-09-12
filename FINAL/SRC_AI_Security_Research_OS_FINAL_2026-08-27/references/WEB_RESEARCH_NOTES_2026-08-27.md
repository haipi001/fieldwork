# Web research notes — 2026-08-27

These notes are for Codex/developers to know which design assumptions were externally re-checked.

## Report platforms
- HackerOne: asset type, report template, weakness, optional severity, PoC consisting of vulnerability/steps/impact, attachments, and program custom fields.
- Bugcrowd: title, target, VRT technical severity, location, description organized around overview/walkthrough+PoC/evidence/impact, attachments.
- Intigriti: title, asset, endpoint, vulnerability type, CVSS/manual severity, reproducible report, explicit impact; recommendation/testing IP are useful additional fields.
- Immunefi: platform-wide rules coexist with project rules; mainnet/public-testnet testing is prohibited by current rules; incomplete required PoCs/low-quality automated reports can be rejected.

## Standards
- SARIF 2.1.0 remains the OASIS standard interchange for static-analysis outputs.
- FIRST CVSS v4.0 is current.
- CWE current site provides the weakness taxonomy.
- OWASP WSTG remains a comprehensive web testing methodology.
- OWASP API Security Top 10 current published edition is 2023.
- OSV schema current page shows v1.9.0 dated 2026-08-06.

## Open source design signals
- Strix currently exposes agent/runtime/tools/report/skills separation and autonomous validated PoC workflows.
- pentest-ai emphasizes machine-oracle re-verification and proof capsules.
- PentAGI remains a self-hosted autonomous pentest orchestration reference.
- SWE-agent ACI documentation stresses concise, purpose-built tool interfaces rather than raw shell context.

Always re-check project license/version before vendoring or linking a dependency.
