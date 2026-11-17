"""Verify compact mobile navigation and keyboard access."""
import copy
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-mobile-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from backend.sample import DEMO_GUIDE
from backend.storage import db, dumps, now


def run():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    output = Path(__file__).resolve().parent.parent / 'test-results'
    output.mkdir(exist_ok=True)
    checked, errors = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            expect(page.locator('#toggle-navigation')).to_have_attribute('aria-expanded', 'false')
            expect(page.locator('#chapter-search')).not_to_be_visible()
            mobile_height = page.locator('.sidebar').bounding_box()['height']
            assert mobile_height < 150, mobile_height
            page.screenshot(path=str(output / 'mobile-compact.png'))
            page.locator('#toggle-navigation').focus()
            page.keyboard.press('Enter')
            expect(page.locator('#chapter-search')).to_be_visible()
            page.locator('#chapter-search').fill('不存在的章节')
            page.locator('#clear-history-filters').click()
            expect(page.locator('.chapter-nav')).to_have_count(1)
            page.keyboard.press('Escape')
            expect(page.locator('#toggle-navigation')).to_be_focused()
            expect(page.locator('#chapter-search')).not_to_be_visible()
            page.locator('#toggle-navigation').click()
            page.locator('.chapter-nav').click()
            expect(page.locator('#toggle-navigation')).to_have_attribute('aria-expanded', 'false')
            expect(page.locator('#main')).to_be_focused()
            checked.append('compact mobile navigation supports Enter, Escape, search and returning to reading')
            for width in (320, 390, 720):
                page.set_viewport_size({'width': width, 'height': 844})
                page.locator('#toggle-navigation').click()
                expect(page.locator('#open-settings')).to_be_visible()
                page.locator('#open-settings').click()
                expect(page.locator('#settings-dialog')).to_be_visible()
                page.locator('[data-close="settings-dialog"]').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                page.locator('#toggle-navigation').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate("document.documentElement.style.fontSize='32px'")
            page.locator('#toggle-navigation').click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '200% mobile text overflow'
            assert page.evaluate("""() => {
                const range = document.createRange();
                range.selectNodeContents(document.querySelector('.brand > span:last-child').firstChild);
                return range.getClientRects().length;
            }""") == 1, 'The brand title should not be squeezed into a vertical column at 200% text size'
            page.screenshot(path=str(output / 'mobile-expanded-200.png'), full_page=True)
            page.evaluate("document.documentElement.style.fontSize=''")
            checked.append('320px, 390px, 720px and doubled mobile text keep tools reachable without horizontal overflow')
            page.set_viewport_size({'width': 1440, 'height': 1050})
            expect(page.locator('#toggle-navigation')).not_to_be_visible()
            expect(page.locator('#chapter-search')).to_be_visible()
            page.screenshot(path=str(output / 'desktop-navigation.png'), full_page=True)
            checked.append('desktop navigation remains expanded after a mobile resize')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'mobile_sidebar_height_px': mobile_height, 'javascript_errors': errors}
        (output / 'mobile-navigation-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
