"""Presentation options; canonical evidence is never rewritten."""
import re
from pydantic import BaseModel, ConfigDict
from typing import Literal
from reporting import redact_structure

class ReportOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template: Literal['detailed','summary'] = 'detailed'
    language: Literal['en','zh'] = 'en'
    redaction: Literal['standard','strict'] = 'standard'


def strict_redact(value):
    if isinstance(value,dict): return {k:strict_redact(v) for k,v in value.items()}
    if isinstance(value,list): return [strict_redact(v) for v in value]
    if not isinstance(value,str): return value
    value=re.sub(r'https?://[^\s<>"\)]+','[URL REDACTED]',value)
    value=re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b','[EMAIL REDACTED]',value)
    return re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','[IP REDACTED]',value)


def present(model,content,check,options):
    model,check=redact_structure(model),dict(check)
    if options.template=='summary':
        content=f"# {model['title']}\n\n## Summary\n{model.get('summary','')}\n\n## Impact\n{model.get('impact',{}).get('description','')}\n\n## Severity\n{model.get('severity','')}\n\n## Counterevidence / Negative Control\n{model.get('counterevidence',{}).get('summary','')}\n\n## Evidence\n"+'\n'.join(f"- {e['id']}: {e.get('summary','')}" for e in model.get('evidence',[]))
    if options.language=='zh':
        headings={'Target / Asset':'目标 / 资产','Vulnerable Location':'漏洞位置','Summary':'摘要','Root Cause':'根因','Steps to Reproduce':'复现步骤','Prerequisites':'前置条件','Impact':'影响','Counterevidence / Negative Control':'反证 / 负对照','Severity':'严重度','Evidence':'证据','Remediation':'修复建议','Completeness':'完整度'}
        for en,zh in headings.items(): content=content.replace('## '+en+'\n','## '+zh+'\n')
        content+='\n\n> 章节标题使用中文；原始研究内容保持原文，未调用模型翻译。\n'
    if options.redaction=='strict':
        model,content=strict_redact(model),strict_redact(content)
        check={**check,'ready':False,'presentation_only':True,'redaction_notice':'URLs, email and IPv4 redacted; runnable proof attachments excluded. Review required.'}
    return model,content,check
