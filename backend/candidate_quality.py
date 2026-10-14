"""Candidate admission contracts; discovery facts alone are not vulnerability hypotheses."""
SECURITY_OBSERVATIONS = {
    'authorization': 'authorization', 'http.authorization': 'authorization',
    'http.reflection': 'reflection', 'scanner.template_match': 'scanner_match',
    'code.static_finding': 'code_review', 'code.secret_candidate': 'secret_exposure',
    'code.dependency_vulnerability': 'dependency', 'code.misconfiguration': 'misconfiguration',
    'agent.candidate_finding': 'agent_review',
}
AGENT_CATEGORIES = {
    'authorization', 'authentication', 'injection', 'xss', 'ssrf', 'path_traversal',
    'sensitive_data_exposure', 'misconfiguration', 'business_logic', 'session_security',
}
REQUIRED_CLAIM_FIELDS = ('security_boundary', 'expected_behavior', 'observed_behavior', 'impact_hypothesis', 'verification_method')


def security_category(observation):
    kind = observation['observation_type']
    if kind.startswith('web3.hypothesis.'):
        return kind.removeprefix('web3.hypothesis.')
    return SECURITY_OBSERVATIONS.get(kind)


def agent_claim_errors(item):
    errors = [f'missing_{key}' for key in REQUIRED_CLAIM_FIELDS if not isinstance(item.get(key), str) or len(item[key].strip()) < 8]
    if item.get('category') not in AGENT_CATEGORIES:
        errors.append('unsupported_security_category')
    if not isinstance(item.get('observation_ids'), list) or not item['observation_ids'] or any(not isinstance(x, str) for x in item['observation_ids']):
        errors.append('specific_observation_references_required')
    return errors


# Recommendations only: never changes candidate status or verification eligibility.
INFORMATION_CATEGORIES = {
    'correlated_observation', 'web_hypothesis', 'legacy_import', 'business', 'data',
    'product', 'solution', 'technology', 'business/offering', 'business/partners',
    'company_affiliation', 'documentation', 'navigation', 'offerings',
    'product_positioning', 'regulatory/compliance', 'regulatory_status', 'service',
    'service catalog', 'site_structure', 'technical', 'website_content',
    'information_discovery', 'technology_stack', 'scope', 'site accessibility', 'client detection',
}
HTTP_READ_CATEGORIES = {'authorization', 'authentication', 'access_control', 'cwe-639', 'idor'}


def candidate_next_action(candidate):
    category = str(candidate.get('category', '')).lower()
    evidence = candidate.get('evidence_ids')
    property_test = candidate.get('mode') == 'web3' and category == 'web3_property_violation'
    http_read = candidate.get('mode') == 'traditional' and category in HTTP_READ_CATEGORIES
    method = 'web3_property' if property_test else 'http_workbench' if http_read else 'manual_review'
    if candidate.get('status') == 'needs_evidence' or not isinstance(evidence, list) or not evidence:
        stage, label, action = 'needs_evidence', '先补证据', '查看缺少的材料'
        reason = '尚未关联来源证据，或已被标记为证据不足；不能把描述当作复验结果。'
        materials = ['支持安全假设的请求、响应、源码位置或属性反例', '正常行为与异常行为的具体差别']
    elif property_test and candidate.get('status') == 'reproduced':
        stage, label, action = 'impact', '补齐影响与规则', '审阅规则与影响'
        reason = '属性已复现，仍需审阅最新规则、部署一致性、反证及实际影响。'
        materials = ['最新已授权项目规则与目标范围', '根因、代码位置及可复核影响', '部署对齐和反证材料']
    elif category in INFORMATION_CATEGORIES:
        stage, label, action = 'triage', '先确认安全问题', '审阅并分诊'
        reason = '分类更接近信息观察。先确认是否存在具体的非公开数据、权限或资产约束被违反。'
        materials = ['被违反的安全边界', '具体受影响对象和可能影响；若无则归为普通观察']
    elif property_test or http_read:
        stage, label, action = 'supported', '有复验入口', '配置两轮复验'
        reason = '已有对应验证向导；配置通过范围和证据检查后才能执行。'
        materials = (['本地 Foundry 源码与失败属性', '两个独立 seed 的反例', '本地运行环境与授权范围'] if property_test else
                     ['JSON 对象接口与对象所有者字段', '两个授权测试身份及身份查询接口', '未登录负对照和剩余请求预算'])
    else:
        stage, label, action = 'manual', '需要专用验证', '查看验证准备项'
        reason = '当前类别尚无直接适用的自动复验向导；需要按漏洞类型建立验证方法。'
        materials = ['最小复现步骤与正常对照', '源码或请求级证据', '适用的专用验证方法及影响证明']
    return {'stage': stage, 'label': label, 'action': action, 'reason': reason,
            'materials': materials, 'method': method, 'automatic_execution': False}
