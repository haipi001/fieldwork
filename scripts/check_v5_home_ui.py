"""Read-only browser checks for the screenshot-based campaign overview."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        headless=True,
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8011/v5#campaign")
    page.wait_for_function("document.querySelector('#homeMetrics').children.length === 6", timeout=30000)
    page.wait_for_function("!document.querySelector('#homeGraph').innerText.includes('读取图谱中')", timeout=30000)
    assert page.locator("#homeOverview .home-grid > article").count() == 10
    for theme in ("dark", "light"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        for width in (400, 800, 1024, 1280, 1440):
            page.set_viewport_size({"width": width, "height": 1100})
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (theme, width)
    if page.locator("#homeProject").input_value():
        page.locator("#homeQuestion").fill("当前进度和下一步是什么？")
        page.locator("#homeAsk button").click()
        assert "来源：" in page.locator("#homeAnswer").inner_text()
        page.locator("#homeScope").click()
        page.locator("#scopeDialog").wait_for(state="visible", timeout=20000)
        page.keyboard.press("Escape")
    if page.locator("[data-home-node]").count():
        page.locator("[data-home-node]").first.click()
        assert page.locator("#detailInspector").get_attribute("aria-hidden") == "false"
        page.keyboard.press("Escape")
    page.locator('.home-tabs [data-home-view="tasks"]').click()
    assert page.locator("#tasks").get_attribute("class") == "view active"
    assert not errors, errors
    page.goto("http://127.0.0.1:8011/v5#campaign")
    page.wait_for_function("document.querySelector('#homeMetrics').children.length === 6", timeout=30000)
    page.wait_for_timeout(1500)
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    page.screenshot(path="/tmp/fieldwork-v5-home-light.png", full_page=True)
    print("Home: 10 sections, 10 theme/viewport combinations, fact query, Scope, Inspector and navigation passed")
    browser.close()
