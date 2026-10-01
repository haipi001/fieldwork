"""Bounded, credential-free verification oracles in a separate OS process.

This process has no database connection, model access, or application imports.  It
does not claim to verify an authorization vulnerability: its oracle is only the
declared status relationship between a positive and a negative-control URL.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _url(value: str) -> None:
    parsed = urlsplit(value)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL must use a literal loopback IP and valid port") from error
    if (parsed.scheme != "http" or not address.is_loopback or not port
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("URL must be credential-free loopback HTTP")


def _status(url: str) -> int:
    _url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "Fieldwork-V5-Verifier/1"})
    try:
        with opener.open(request, timeout=4) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _package_applicability(contract: dict) -> dict:
    fingerprint = contract.get("fingerprint", {})
    affected = contract.get("affected", [])
    observations = contract.get("observed_packages", [])
    if (not isinstance(fingerprint, dict) or not isinstance(affected, list)
            or not isinstance(observations, list) or len(affected) > 200
            or len(observations) > 200):
        raise ValueError("invalid package applicability input")
    ecosystem, name, version = (fingerprint.get(key) for key in ("ecosystem", "name", "version"))
    if any(not isinstance(value, str) or not value or len(value) > 300
           for value in (ecosystem, name, version)):
        raise ValueError("package fingerprint must contain an exact version")
    observed_versions = set()
    for item in observations:
        if not isinstance(item, dict) or not isinstance(item.get("purl"), str):
            raise ValueError("invalid package observation")
        purl = item["purl"].split("#", 1)[0].split("?", 1)[0]
        if not purl.startswith("pkg:") or "/" not in purl or "@" not in purl:
            raise ValueError("package observation requires a versioned PURL")
        purl_type, remainder = purl[4:].split("/", 1)
        purl_name, purl_version = remainder.rsplit("@", 1)
        purl_name, purl_version = unquote(purl_name), unquote(purl_version)
        if (not purl_type or not purl_name or not purl_version
                or item.get("pkg_name") != purl_name
                or item.get("installed_version") != purl_version):
            raise ValueError("package observation disagrees with its PURL")
        if purl_type.casefold() == ecosystem.casefold() and purl_name.casefold() == name.casefold():
            observed_versions.add(purl_version)
    listed = any(
        isinstance(item, dict) and str(item.get("ecosystem", "")).casefold() == ecosystem.casefold()
        and str(item.get("name", "")).casefold() == name.casefold()
        and isinstance(item.get("versions"), list) and version in item["versions"]
        for item in affected
    )
    source_consistent = observed_versions == {version}
    status = "verified" if source_consistent and listed else "inconclusive"
    classification = "positive" if status == "verified" else "invalid_precondition"
    return {
        "status": status, "classification": classification,
        "summary": "Exact advisory version checked against a hashed non-synthetic Trivy artifact",
        "preconditions_valid": source_consistent and listed,
        "counterevidence_checked": True, "interrupted": False, "attempts": 1,
        "oracle": {"kind": "package_applicability_v1", "ecosystem": ecosystem,
                   "name": name, "version": version,
                   "observed_versions": sorted(observed_versions), "exact_affected_version_listed": listed,
                   "source_artifact_sha256": contract.get("source_artifact_sha256"),
                   "advisory_record_sha256": contract.get("advisory_record_sha256")},
    }


def _loopback_http_status(contract: dict) -> dict:
    positive, negative = contract.get("url"), contract.get("negative_control_url")
    expected, expected_negative = contract.get("expected_status"), contract.get("negative_control_status")
    if (not isinstance(positive, str) or not isinstance(negative, str) or positive == negative
            or type(expected) is not int or type(expected_negative) is not int
            or not 200 <= expected <= 499 or not 200 <= expected_negative <= 499
            or expected == expected_negative):
        raise ValueError("distinct URLs and distinct HTTP status oracles are required")
    _url(positive)
    _url(negative)
    observed_positive, observed_negative = [], []
    for _ in range(2):
        observed_positive.append(_status(positive))
        observed_negative.append(_status(negative))
    control_valid = observed_negative == [expected_negative] * 2
    stable = len(set(observed_positive)) == 1 and len(set(observed_negative)) == 1
    if not control_valid or not stable:
        classification, status = "invalid_precondition", "inconclusive"
    elif observed_positive == [expected] * 2:
        classification, status = "positive", "verified"
    else:
        classification, status = "healthy_negative", "refuted"
    return {
        "status": status, "classification": classification,
        "summary": "Two-round loopback HTTP status replay with a negative control",
        "preconditions_valid": control_valid and stable,
        "counterevidence_checked": True, "interrupted": False, "attempts": 2,
        "oracle": {"kind": "loopback_http_status_v1", "positive_statuses": observed_positive,
                   "negative_control_statuses": observed_negative,
                   "expected_status": expected, "expected_negative_control_status": expected_negative},
    }


def evaluate(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("verifier input must be an object")
    kind = contract.get("type")
    if kind == "loopback_http_status_v1":
        result = _loopback_http_status(contract)
    elif kind == "package_applicability_v1":
        result = _package_applicability(contract)
    else:
        raise ValueError("unsupported local verifier contract")
    canary = os.environ.get("FIELDWORK_DENIED_CANARY")
    if not canary:
        raise ValueError("missing isolation canary")
    try:
        with open(canary, "rb") as stream:
            stream.read(1)
    except PermissionError:
        file_read_denied = True
    else:
        file_read_denied = False
    network_denied = None
    if kind == "package_applicability_v1":
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
        except PermissionError:
            network_denied = True
        else:
            network_denied = False
    return {"pid": os.getpid(), "ppid": os.getppid(), "result": result,
            "isolation_probe": {"file_read_denied": file_read_denied,
                                "network_denied": network_denied}}


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("verifier input too large")
        value = json.loads(raw)
        print(json.dumps(evaluate(value), sort_keys=True, separators=(",", ":")))
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}))
        raise SystemExit(2)
