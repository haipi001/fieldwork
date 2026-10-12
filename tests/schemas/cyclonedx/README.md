# CycloneDX validation fixtures

Official upstream schema snapshots, downloaded 2026-10-12 with verified TLS. `SOURCES.json` records source URLs and exact byte SHA256 values; `LICENSE` retains the upstream Apache 2.0 license. These are development validation fixtures, not executable package code.

Run `python scripts/validate_python_sbom.py <export.json>` after installing requirements-dev.txt. Validation verifies local schema digests and resolves references offline; it does not retrieve remote schemas. A passing schema does not prove package integrity, completeness across excluded domains, provenance, vulnerability status or license compliance.
