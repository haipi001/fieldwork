"""Monitor-scoped, revocable receiver for browser request metadata."""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import secrets
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

import final_core as core

router = APIRouter(prefix='/api/v1/agent-audit', tags=['AI Browser Audit'])
HOSTS = {'chatgpt.com': 'ChatGPT', 'claude.ai': 'Claude',
         'gemini.google.com': 'Gemini', 'copilot.microsoft.com': 'Copilot',
         'www.perplexity.ai': 'Perplexity', 'grok.com': 'Grok',
         'chat.deepseek.com': 'DeepSeek', 'www.doubao.com': 'Doubao',
         'yuanbao.tencent.com': 'Yuanbao'}
EXTENSION = Path(__file__).parent / 'collectors/browser-ai'


def init_db(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS agent_browser_connections (
        id TEXT PRIMARY KEY, audit_id TEXT NOT NULL REFERENCES agent_audits(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
        sequence INTEGER NOT NULL DEFAULT 0, event_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, last_seen_at TEXT);
        CREATE INDEX IF NOT EXISTS agent_browser_audit ON agent_browser_connections(audit_id);''')


def local_request(request):
    try:
        if not request.client or not ipaddress.ip_address(request.client.host).is_loopback:
            raise ValueError()
    except ValueError:
        raise HTTPException(403, '浏览器采集仅允许本机连接')
    if request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise HTTPException(403, '浏览器采集仅允许本机接收地址')
    origin = request.headers.get('origin', '')
    if origin and not (re.fullmatch(r'chrome-extension://[a-p]{32}', origin) or origin == str(request.base_url).rstrip('/')):
        raise HTTPException(403, '不允许此来源连接浏览器采集器')


class Event(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timestamp: str = Field(max_length=80)
    host: str = Field(max_length=100)
    method: str = Field(pattern=r'^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|CONNECT|TRACE)$')
    kind: str = Field(pattern=r'^(navigation|request)$')
    phase: str = Field(pattern=r'^(started|completed|error)$')
    status_code: int | None = Field(default=None, ge=100, le=599)


class Batch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sequence: int = Field(ge=1, le=9007199254740991)
    events: list[Event] = Field(default_factory=list, max_length=100)


def view(db, audit_id):
    rows = db.execute("SELECT id,status,event_count,last_seen_at FROM agent_browser_connections WHERE audit_id=? ORDER BY created_at DESC", (audit_id,)).fetchall()
    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        item = dict(row)
        if item['status'] == 'active':
            seen = datetime.fromisoformat(item['last_seen_at']) if item['last_seen_at'] else None
            item['status'] = 'connected' if seen and now - seen < timedelta(seconds=90) else 'stale' if seen else 'waiting'
        result.append(item)
    return {'connections': result, 'event_count': sum(row['event_count'] for row in rows),
            'status': 'connected' if any(row['status'] == 'connected' for row in result) else 'not_connected',
            'scope': '已授权 AI 域名的请求元数据；不读取路径、参数、正文、Cookie 或提示词',
            'hosts': HOSTS}


@router.post('/monitor/{audit_id}/browser/package')
def package(audit_id: str, request: Request):
    import agent_audit as audit
    local_request(request)
    with audit.MONITOR_LOCK:
        with core.connect() as db:
            monitor = db.execute('SELECT * FROM agent_monitors WHERE audit_id=?', (audit_id,)).fetchone()
            if not monitor or monitor['collector_kind'] != 'desktop' or monitor['status'] != 'active':
                raise HTTPException(409, '请先开始本机监控，再下载浏览器采集器')
            token = secrets.token_urlsafe(32)
            connection = core.uid('browser')
            db.execute('INSERT INTO agent_browser_connections(id,audit_id,token_hash,status,created_at) VALUES(?,?,?,?,?)',
                (connection, audit_id, hashlib.sha256(token.encode()).hexdigest(), 'active', core.utcnow()))
        config = {'endpoint': 'http://127.0.0.1:8000/api/v1/agent-audit/browser/events',
                  'token': token, 'hosts': HOSTS, 'connectionId': connection}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in ['manifest.json', 'worker.js', 'metadata.js', 'popup.html', 'popup.js']:
                archive.writestr(name, (EXTENSION / name).read_bytes())
            archive.writestr('config.js', 'const FIELDWORK_BROWSER_CONFIG = ' + json.dumps(config, ensure_ascii=False) + ';')
            archive.writestr('README.txt', '解压后，在 Chrome 扩展页面开启开发者模式并加载此目录。点击扩展按钮，授权需要记录的 AI 站点。配对仅用于当前 Fieldwork 监控，结束后失效；ZIP 含本机会话配对信息，请勿分享。\n')
        return Response(buffer.getvalue(), media_type='application/zip', headers={
            'Content-Disposition': 'attachment; filename="fieldwork-browser-ai.zip"', 'Cache-Control': 'no-store'})


@router.post('/monitor/{audit_id}/browser/{connection_id}/revoke')
def revoke(audit_id: str, connection_id: str, request: Request):
    import agent_audit as audit
    local_request(request)
    with audit.MONITOR_LOCK:
        with core.connect() as db:
            cursor = db.execute("UPDATE agent_browser_connections SET status='revoked' WHERE id=? AND audit_id=?", (connection_id, audit_id))
            if not cursor.rowcount:
                raise HTTPException(404, '浏览器连接不存在')
    return {'status': 'revoked'}


@router.post('/browser/events')
def receive(body: Batch, request: Request):
    import agent_audit as audit
    local_request(request)
    auth = request.headers.get('authorization', '')
    if not auth.startswith('Bearer ') or len(auth) > 200:
        raise HTTPException(401, '浏览器采集器尚未配对')
    token_hash = hashlib.sha256(auth[7:].encode()).hexdigest()
    with audit.MONITOR_LOCK:
        with core.connect() as db:
            connection = db.execute('SELECT * FROM agent_browser_connections WHERE token_hash=?', (token_hash,)).fetchone()
            if not connection or connection['status'] != 'active':
                raise HTTPException(401, '浏览器配对已失效')
            monitor = db.execute('SELECT * FROM agent_monitors WHERE audit_id=?', (connection['audit_id'],)).fetchone()
            if monitor['status'] != 'active':
                raise HTTPException(409, '监控暂停或结束，本批记录不会接收')
            if body.sequence <= connection['sequence']:
                # Idempotent retries after a lost response must not duplicate evidence.
                return {'accepted': 0, 'sequence': connection['sequence'], 'duplicate': True}
            identity = core.load(audit.audit_row(db, monitor['audit_id'])['identity_json'])
        now = datetime.now(timezone.utc)
        events, dropped = [], 0
        for event in body.events:
            if event.host not in HOSTS:
                raise HTTPException(422, '事件域名不在浏览器 AI 采集范围内')
            try:
                time = audit.timestamp(event.timestamp)
                if time < audit.timestamp(monitor['started_at']) or time > now + timedelta(seconds=60):
                    raise ValueError()
            except ValueError:
                raise HTTPException(422, '浏览器事件时间不在本次监控范围内')
            if monitor['browser_accept_after'] and time < audit.timestamp(monitor['browser_accept_after']):
                dropped += 1
                continue
            service = HOSTS[event.host]
            events.append({'timestamp': event.timestamp, 'actor': service + ' (Browser)',
                'session_id': identity['session_id'], 'source_type': 'browser',
                'action_type': 'navigate' if event.kind == 'navigation' else 'request',
                'request_method': event.method, 'network_host': event.host,
                'status': 'observed', 'attribution_method': 'browser_metadata',
                'command_category': 'browser_request_observed',
                'resource': f'浏览器请求观察：{service} · {event.method} · {event.phase} · HTTP {event.status_code or "UNKNOWN"}'})
        if events:
            audit.import_events(monitor['audit_id'], audit.ImportInput(content=core.dump({'events': events}),
                provenance='agent_supplied', source_name='浏览器采集器元数据（未签名）'))
        with core.connect() as db:
            db.execute('UPDATE agent_browser_connections SET sequence=?,event_count=event_count+?,last_seen_at=? WHERE id=?',
                (body.sequence, len(events), core.utcnow(), connection['id']))
        return {'accepted': len(events), 'sequence': body.sequence, 'duplicate': False, 'dropped': dropped}
