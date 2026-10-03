"""Isolated all-view language/theme and layout checks; no real API writes."""
from pathlib import Path
from urllib.parse import urlparse
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ('campaign', 'graph', 'hypotheses', 'agents', 'evolution', 'tasks', 'runners',
         'tools', 'sentinel', 'events', 'incidents', 'evidence', 'verification',
         'findings', 'reports', 'runtime', 'extensions', 'settings')


def run():
    errors, writes, untranslated, overflow = [], [], [], []
    def route_request(route):
        req, path = route.request, urlparse(route.request.url).path
        if req.method != 'GET':
            writes.append(path)
            route.abort()
        elif path == '/v5':
            route.fulfill(path=str(ROOT / 'templates/v5.html'), content_type='text/html')
        elif path.startswith('/static/'):
            file = (ROOT / path.lstrip('/')).resolve()
            if file.is_relative_to(ROOT / 'static') and file.is_file():
                route.fulfill(path=str(file))
            else:
                route.fulfill(status=404)
        elif path == '/api/v1/findings':
            route.fulfill(json={'verified': [], 'candidates': []})
        elif path in {'/api/v1/engagements', '/api/v1/task-center', '/api/v1/agent-audit/audits', '/api/v1/capabilities'}:
            route.fulfill(json=[])
        else:
            route.fulfill(status=503, json={'detail': 'Fixture service unavailable'})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        for width in (390, 1280):
            page = browser.new_page(viewport={'width': width, 'height': 960})
            page.on('pageerror', lambda e: (errors.append(str(e)), print('SCRIPT ERROR:', e)))
            page.route('**/*', route_request)
            page.goto('http://127.0.0.1:8012/v5')
            page.wait_for_function("!document.querySelector('#lastUpdated').textContent.includes('尚未')")
            for lang in ('zh', 'en', 'zh'):
                if (page.locator('html').get_attribute('lang') or '').startswith('en') != (lang == 'en'):
                    page.evaluate("document.querySelector('#languageToggle').click()")
                for theme in ('dark', 'light'):
                    if page.locator('html').get_attribute('data-theme') != theme:
                        page.evaluate("document.querySelector('#themeToggle').click()")
                    for view in VIEWS:
                        page.evaluate("id => document.querySelector(`[data-view='${id}']`).click()", view)
                        page.wait_for_timeout(35)
                        delta = page.evaluate('document.documentElement.scrollWidth - innerWidth')
                        if delta > 1:
                            nodes = page.evaluate("""() => [...document.querySelectorAll('.view.active *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1 && getComputedStyle(e).position!=='absolute').slice(0,8).map(e=>({tag:e.tagName,id:e.id,cls:e.className,text:e.textContent.slice(0,100)}))""")
                            overflow.append([width, lang, theme, view, delta, nodes])
                        if lang == 'en':
                            found = page.evaluate("""() => {
                              const roots=[document.querySelector('.view.active'),document.querySelector('.topbar')];
                              const output=[];
                              for(const root of roots){const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
                                while(walker.nextNode()){const n=walker.currentNode;if(n.parentElement.getClientRects().length&&/[\\u3400-\\u9fff]/.test(n.textContent))output.push(n.textContent.trim());}}
                              for(const root of roots)for(const node of root.querySelectorAll('[title],[aria-label],[placeholder]')){
                                if(!node.getClientRects().length)continue;
                                for(const key of ['title','aria-label','placeholder']){const value=node.getAttribute(key)||'';if(/[\\u3400-\\u9fff]/.test(value))output.push(value);}
                              }
                              return [...new Set(output)].filter(s=>!['中','English → 中文'].includes(s));
                            }""")
                            if found:
                                untranslated.append([view, found])
            # A language switch must not reset an in-progress draft or send writes.
            page.evaluate("document.querySelector('[data-view=campaign]').click();document.querySelector('#newResearch').click()")
            page.locator('#researchName').fill('我的项目 <原文>')
            page.locator('#researchTarget').fill('https://authorized.example/path')
            page.evaluate("document.querySelector('#languageToggle').click()")
            assert page.locator('#researchName').input_value() == '我的项目 <原文>'
            assert page.locator('#researchTarget').input_value() == 'https://authorized.example/path'
            assert page.locator('#researchDialog').evaluate('(e)=>e.open')
            page.keyboard.press('Escape')
            page.close()
        browser.close()
    print(json.dumps({'errors': errors, 'writes': writes, 'overflow': overflow, 'untranslated': untranslated}, ensure_ascii=False, indent=2))
    assert not errors and not writes and not overflow and not untranslated
    print('PASS: 18 views × 2 languages × 2 themes × 2 widths, return to Chinese, draft preservation, no writes')


if __name__ == '__main__':
    run()
