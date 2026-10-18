"""Evaluate object reads against business rules in the confirmed scope snapshot.

Rules are project inputs, never inferred from owner metadata or supplied by a
replay success claim. Exact object URLs deliberately avoid wildcard ambiguity.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def select_rule(scope, target):
    rules = scope.get('http_object_read_rules', [])
    if not isinstance(rules, list):
        return None, 'invalid_rules'
    matches = [rule for rule in rules if isinstance(rule, dict) and rule.get('target') == target]
    if len(matches) != 1:
        return None, 'ambiguous_rule' if matches else 'missing_rule'
    rule = matches[0]
    if (not isinstance(rule.get('source'), str) or not rule['source'].strip()
            or rule.get('access') not in ('owner_only', 'allowlist', 'public')):
        return None, 'invalid_rule'
    allowed = rule.get('allowed_principals', [])
    if not isinstance(allowed, list) or any(type(value) is not str or not value for value in allowed):
        return None, 'invalid_principals'
    if rule['access'] != 'allowlist' and allowed:
        return None, 'conflicting_rule'
    return rule, None


def evaluate(scope, target, rounds, assertion):
    from traditional_runtime import json_scalar
    result = {'schema': 'http-business-boundary/1', 'target': target,
              'scope_sha256': digest(scope), 'status': 'unknown', 'reason': 'missing_rule'}
    rule, reason = select_rule(scope, target)
    if rule is None:
        return {**result, 'reason': reason}
    allowed = rule.get('allowed_principals', [])
    if assertion is None or len(rounds) != 2:
        return {**result, 'reason': 'missing_identity_observations'}
    decisions = []
    principals = []
    for responses in rounds:
        owner = json_scalar(responses['baseline_identity'], assertion.principal_field)
        other = json_scalar(responses['attack_identity'], assertion.principal_field)
        if owner is None or other is None or owner == other:
            return {**result, 'reason': 'invalid_principals'}
        principals.append((owner, other))
        decisions.append(rule['access'] == 'public' or (rule['access'] == 'allowlist' and other in allowed))
    if len(set(principals)) != 1:
        return {**result, 'reason': 'unstable_principals'}
    result.update(rule_sha256=digest(rule), owner_sha256=digest(principals[0][0]),
                  principal_sha256=digest(principals[0][1]))
    if all(decisions):
        return {**result, 'status': 'permitted', 'reason': 'access_permitted_by_rule'}
    return {**result, 'status': 'denied', 'reason': 'access_denied_by_rule'}


def receipt_boundary_valid(artifact, scope, target):
    """Require persisted execution to bind a denied read to the exact scope."""
    boundary = artifact.get('business_boundary', {})
    if not isinstance(boundary, dict):
        return False
    rule, _ = select_rule(scope, target)
    if rule is None or rule['access'] == 'public':
        return False
    allowed = rule.get('allowed_principals', [])
    if not isinstance(allowed, list) or any(type(value) is not str or not value for value in allowed):
        return False
    if (rule['access'] == 'owner_only' and allowed
            or boundary.get('principal_sha256') in [digest(value) for value in allowed]):
        return False
    principal, owner = boundary.get('principal_sha256'), boundary.get('owner_sha256')
    if not all(isinstance(value, str) and len(value) == 64 for value in (principal, owner)) or principal == owner:
        return False
    return (boundary.get('schema') == 'http-business-boundary/1'
            and boundary.get('status') == 'denied' and boundary.get('target') == target
            and boundary.get('scope_sha256') == digest(scope)
            and boundary.get('rule_sha256') == digest(rule))
