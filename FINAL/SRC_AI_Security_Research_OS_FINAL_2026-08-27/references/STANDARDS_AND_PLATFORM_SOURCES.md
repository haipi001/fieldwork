# Standards / Platform Sources

Checked: 2026-08-27

## Platform report requirements
- HackerOne submitting reports  
  https://docs.hackerone.com/en/articles/8473994-submitting-reports
- HackerOne report form/templates  
  https://docs.hackerone.com/en/articles/8496313-submit-report-form
- Bugcrowd reporting a bug  
  https://docs.bugcrowd.com/researchers/reporting-managing-submissions/reporting-a-bug/
- Intigriti good report guide  
  https://kb.intigriti.com/en/articles/5379086-how-to-write-and-submit-a-good-report
- Immunefi rules  
  https://immunefi.com/rules/

## Security standards
- SARIF 2.1.0 — OASIS  
  https://www.oasis-open.org/standard/sarifv2-1-os/
- CVSS v4.0 — FIRST  
  https://www.first.org/cvss/v4.0/
- CWE — MITRE  
  https://cwe.mitre.org/
- OWASP WSTG  
  https://owasp.org/www-project-web-security-testing-guide/
- OWASP API Security Top 10  
  https://owasp.org/API-Security/
- OSV Schema  
  https://ossf.github.io/osv-schema/

## 设计结论
- 内部 Finding schema 不能直接等于任一平台表单。
- 平台规则会变化，因此 adapter/config 必须版本化。
- SARIF 用于静态分析结果交换，不足以替代完整 bounty report。
- CVSS 是 severity 的一种表达，不应覆盖平台 VRT / Immunefi program-specific impact rules。
