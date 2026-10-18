"""Isolated browser interactions using real repository UI assets."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_v5_frontend_ui import isolated_page, BASE, CHROME

ADDR='0x'+'12'*20

def check(browser,width,english=False):
    page=isolated_page(browser,viewport={'width':width,'height':900})
    errors=[];writes=[];attachments=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    if english:page.add_init_script("localStorage.setItem('fieldwork-v5-language','en')")
    page.goto(BASE+'/v5')
    value={'import_id':'import-'+'a'*32,'status':'chain_unchecked','finished_at':'2026-10-14','source_path':'/isolated/web3/fixture','provider':'fixture','chain_id':1,'file_count':2,'compiler_version':'0.8.24','contract_name':'Counter','elapsed_seconds':1,'compile':{'status':'compiled'}}
    def post(route):
        writes.append(route.request.post_data_json)
        route.fulfill(status=202,json=value)
    page.route('**/api/v1/web3/contract-imports',post)
    def attach(route):
        attachments.append(route.request.post_data_json)
        route.fulfill(json={**value,'authorization_granted':False})
    page.route('**/api/v1/web3/contract-imports/*/attach',attach)
    page.route('**/api/v1/web3/contract-imports/engagements/ui-project/latest',lambda r:r.fulfill(json={'import':None}))
    page.evaluate("""value=>{window.FieldworkContractImport.open(value);document.querySelector('#executionDialog').showModal()}""",{'id':'ui-project','mode':'web3','target_type':'contract','normalized_target':ADDR,'raw_target':'https://etherscan.io/address/'+ADDR+'?utm_source=immunefi','chain_id':1})
    page.wait_for_function("document.querySelector('#executionSource').value==='/isolated/web3/fixture'")
    assert writes[0]['address'].endswith('?utm_source=immunefi') and writes[0]['mode']=='auto'
    assert page.evaluate('window.FieldworkContractImport.executionContext().execution_mode')=='read_only'
    page.locator('#contractImportReadOnly').uncheck()
    assert page.evaluate('window.FieldworkContractImport.executionContext().execution_mode') is None
    page.locator('#executionRpc').fill('https://etherscan.io/address/'+ADDR)
    assert 'JSON-RPC' in page.locator('#executionMessage').inner_text()
    assert not page.locator('#executionRpc').evaluate('(el)=>el.checkValidity()')
    page.locator('#executionRpc').fill('https://rpc.example.test')
    assert page.locator('#executionRpc').evaluate('(el)=>el.checkValidity()')
    # Injection remains inert in the stage/error view.
    value['contract_name']='<img src=x onerror="window.injected=true">'
    page.locator('#contractImportRefresh').click()
    page.wait_for_function("document.querySelector('#contractImportStatus').textContent.includes('<img')")
    assert page.evaluate('window.injected') is None
    # A finished failed job may retain an old source_path; do not auto-attach it.
    attached_before=len(attachments)
    value.update(status='failed',error={'message':'fixture import failed'})
    page.locator('#contractImportRefresh').click()
    page.wait_for_function("document.querySelector('#contractImportStatus').textContent.includes('fixture import failed')")
    page.wait_for_function("!document.querySelector('#contractImportRefresh').disabled")
    assert len(attachments)==attached_before
    assert 'contract_import_id' not in page.evaluate('window.FieldworkContractImport.executionContext()')
    value.update(status='chain_unchecked')
    value.pop('error',None)
    before=len(writes)
    page.route('**/api/v1/web3/contract-imports/engagements/ui-project/latest',lambda r:r.fulfill(json={'import':value}))
    page.evaluate("value=>window.FieldworkContractImport.open(value)",{'id':'ui-project','mode':'web3','target_type':'contract','normalized_target':ADDR,'chain_id':1})
    assert len(writes)==before, 'Restoring a finished job must not fetch or compile again'
    assert page.evaluate('window.FieldworkContractImport.executionContext().contract_import_id')==value['import_id']
    assert not errors,errors
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    output=Path(__file__).resolve().parents[1]/'build/acceptance/web3-auto-import'
    output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/f'ui-{width}-{english}.png'))
    page.close()


if __name__=='__main__':
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=CHROME,headless=True)
        for width in [400,1280]:
            for english in [False,True]:check(browser,width,english)
        browser.close()
    print('Web3 auto-import UI: 4 layouts, real button handlers, source handoff, read-only toggle, RPC validation and inert remote text passed')
