"""Real browser smoke against an isolated running Fieldwork test service."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get('FIELDWORK_UI_BASE', 'http://127.0.0.1:8918')
OUT = Path(__file__).resolve().parents[1] / 'build' / 'agent-audit'
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome')
    page = browser.new_page(viewport={'width':1494,'height':1100})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.add_init_script("localStorage.setItem('fieldwork-onboarding-0.31.1','ui-test')")
    page.goto(BASE+'/new')
    page.locator('#targetInput').fill('https://unchanged.example/证据')
    page.locator('#languageToggle').click()
    assert page.locator('html').get_attribute('lang') == 'en'
    assert 'New analysis' in page.locator('.nav-link[data-go="new"]').inner_text()
    assert page.locator('#targetInput').input_value() == 'https://unchanged.example/证据'
    page.reload()
    assert page.locator('html').get_attribute('lang') == 'en'
    assert 'New analysis' in page.locator('.nav-link[data-go="new"]').inner_text()
    page.locator('#languageToggle').click()
    assert page.locator('html').get_attribute('lang') == 'zh-CN'
    page.get_by_role('button',name='Web3',exact=True).click()
    assert page.locator('#contextMode').inner_text()=='Web3'
    page.locator('[data-mode="agent_audit"]').click()
    page.locator('#agentAuditComposer').wait_for(state='visible')
    assert page.locator('#analysisForm').is_hidden()
    workflow = page.locator('.agent-workflow').first.inner_text()
    assert all(label in workflow for label in ['定义审计', '导入证据', '对账分析', '确认事件'])
    assert page.locator('#agentPolicyDetails').get_attribute('open') is None
    assert page.locator('#agentPolicyPreset').input_value() == 'minimal'
    page.locator('#languageToggle').click()
    assert 'Create a real audit' in page.locator('#agent-new').inner_text()
    page.locator('#languageToggle').click()
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT/'01-new.png'),full_page=True)
    page.locator('#agentDemo').click()
    page.locator('#agentComparison .agent-comparison').first.wait_for()
    assert page.locator('#agentInputSummary').is_visible()
    assert page.locator('#agentImport').is_hidden()
    assert '分析已完成' in page.locator('#agentNextStep').inner_text()
    assert 'CONTRADICTED' in page.locator('#agentComparison').inner_text()
    timeline = page.locator('#agentTimeline').inner_text()
    assert 'SELF REPORT' in timeline and 'POLICY VIOLATION' in timeline and 'CONTRADICTION DETECTED' in timeline
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT/'02-comparison.png'),full_page=True)
    page.locator('.nav-link[data-go="findings"]').click()
    page.locator('#agentFindings .agent-incident').wait_for()
    assert 'VERIFIED' in page.locator('#agentFindings').inner_text()
    assert page.locator('[data-verify-incident]').count() == 0
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT/'03-verified.png'),full_page=True)
    page.locator('.nav-link[data-go="reports"]').click()
    page.locator('#agentPreview').click()
    page.wait_for_function("document.querySelector('#agentReportPreview').textContent.includes('SIMULATED')")
    with page.expect_download() as download:
        page.locator('#agentDownloads a').last.click()
    download.value.save_as(str(OUT/'ui-evidence.zip'))
    page.locator('.nav-link[data-go="settings"]').click()
    capabilities = page.locator('#agentParsers').inner_text()
    assert 'Generic JSON' in capabilities and 'Fieldwork Demo Trace' in capabilities and 'Ed25519' in capabilities
    page.reload()
    assert page.locator('#agent-settings').is_visible()
    for width in [1494,1024,800,390]:
        page.set_viewport_size({'width':width,'height':1000})
        page.locator('.nav-link[data-go="new"]').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'overflow {width}'
        page.screenshot(path=str(OUT/f'width-{width}.png'),full_page=True)
    page.set_viewport_size({'width':1494,'height':1100})
    page.locator('[data-mode="traditional"]').click()
    page.locator('#analysisForm').wait_for(state='visible')
    assert page.locator('#agentAuditComposer').is_hidden()
    page.locator('#targetInput').fill('https://draft.example')
    page.locator('[data-mode="agent_audit"]').click()
    page.locator('[data-mode="traditional"]').click()
    assert page.locator('#targetInput').input_value()=='https://draft.example'
    assert not errors, errors
    print(json.dumps({'browser':'Chrome','flow':'mode → demo → reconciliation → verification → report → ZIP → reload → modes','widths':[1494,1024,800,390],'errors':errors},ensure_ascii=False))
    browser.close()
