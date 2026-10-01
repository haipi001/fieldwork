"""Local read-only checks for the static Fieldwork public site."""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, headless=True)
    for width in (390, 768, 1440):
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until="networkidle")
        assert page.title() == "Fieldwork — 让证据决定结论"
        assert page.locator(".v5-shot img").count() == 2
        page.locator(".v5-shot img").last.scroll_into_view_if_needed()
        page.wait_for_function("[...document.querySelectorAll('.v5-shot img')].every(x => x.complete && x.naturalWidth > 0)")
        assert page.locator(".v5-shot img").evaluate_all("xs => xs.every(x => x.complete && x.naturalWidth > 0)")
        assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--acid').trim()") == "#d6ef6b"
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
        page.get_by_role("tab", name="WEB / API").click()
        assert page.locator("#web-panel").is_visible()
        page.get_by_role("tab", name="WEB3").click()
        assert page.locator("#web3-panel").is_visible()
        assert not errors, errors
        if width in (390, 1440):
            page.goto(BASE, wait_until="networkidle")
            page.evaluate("""async () => {
              document.documentElement.style.scrollBehavior = 'auto';
              for (let y = 0; y < document.documentElement.scrollHeight; y += 650) {
                scrollTo({top: y, behavior: 'instant'});
                await new Promise(resolve => setTimeout(resolve, 75));
              }
              scrollTo({top: 0, behavior: 'instant'});
            }""")
            page.wait_for_function("document.querySelectorAll('.v5-reveal').length === document.querySelectorAll('.v5-reveal.is-visible').length")
            page.wait_for_timeout(1000)
            page.screenshot(path=f"/tmp/fieldwork-site-{width}.png", full_page=True, animations="disabled")
        page.close()
    reduced = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    reduced.goto(BASE, wait_until="networkidle")
    assert reduced.locator(".v5-marquee > div").evaluate("(el) => getComputedStyle(el).animationName") == "none"
    assert reduced.locator(".v5-reveal").count() == 0
    reduced.close()
    browser.close()
print("Site QA passed: 390/768/1440, images, tabs, no overflow or script errors")
