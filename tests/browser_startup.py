"""Exercise startup independence, retry and stale response protection."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-startup-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from tests.fake_provider import FakeProvider


def run():
    provider = FakeProvider().start()
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
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.request.put(url + '/api/settings', data={'base_url': provider.base_url, 'model': 'test-double', 'api_key': 'test-only'})
            delayed = []
            def hold_settings(route):
                if not delayed:
                    delayed.append(route)
                else:
                    route.continue_()
            page.route(url + '/api/settings', hold_settings)
            began = time.monotonic()
            page.goto(url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层', timeout=2000)
            first_content = round(time.monotonic() - began, 3)
            assert delayed, 'Settings request was not held'
            page.locator('#open-settings').click()
            expect(page.locator('#settings-dialog')).to_be_visible()
            page.locator('[data-close="settings-dialog"]').click()
            delayed[0].fulfill(json={'configured': False, 'has_key': False})
            page.wait_for_function('() => state.settings?.configured === true')
            expect(page.locator('#settings-state')).to_have_text('已配置')
            page.unroute(url + '/api/settings', hold_settings)

            attempts = []
            def fail_once(route):
                attempts.append(1)
                if len(attempts) == 1:
                    route.fulfill(status=503, json={'detail': '章节列表暂时不可用'})
                else:
                    route.continue_()
            page.route(url + '/api/chapters', fail_once)
            page.reload()
            expect(page.locator('#load-error')).to_contain_text('章节列表暂时不可用')
            expect(page.locator('#notes')).to_be_disabled()
            expect(page.locator('#question')).to_be_disabled()
            expect(page.locator('#save-notes')).to_be_disabled()
            page.locator('#retry-load').click()
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            expect(page.locator('#load-error')).not_to_be_visible()
            expect(page.locator('#notes')).to_be_enabled()
            expect(page.locator('#question')).to_be_enabled()
            page.unroute(url + '/api/chapters', fail_once)

            snapshot = page.request.get(url + '/api/backups/export').json()
            clone = json.loads(json.dumps(snapshot['chapters'][0]))
            clone['id'] = 'startup-second'
            clone['title'] = '第二章：切换目标'
            clone['created_at'] = datetime.now(timezone.utc).isoformat()
            snapshot['chapters'] = [clone]
            response = page.request.post(url + '/api/backups/import', multipart={'file': {'name': 'chapter.json', 'mimeType': 'application/json', 'buffer': json.dumps(snapshot).encode()}})
            assert response.ok, response.text()
            page.reload()
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            endpoint = url + '/api/chapters/startup-second/study'
            held = []
            page.route(endpoint, lambda route: held.append(route))
            page.locator('[data-chapter="startup-second"]').click()
            expect(page.locator('#navigation-status')).to_be_visible()
            page.locator('[data-chapter="demo-network-layer"]').click()
            assert held
            held.pop().fulfill(status=503, json={'detail': '过期的加载错误'})
            expect(page.locator('#navigation-status')).not_to_be_visible()
            expect(page.locator('#load-error')).not_to_be_visible()
            page.unroute(endpoint)

            failures = []
            def fail_detail(route):
                failures.append(1)
                if len(failures) == 1:
                    route.fulfill(status=503, json={'detail': '章节读取失败，请重试'})
                else:
                    route.continue_()
            page.route(endpoint, fail_detail)
            page.locator('[data-chapter="startup-second"]').click()
            expect(page.locator('#load-error')).to_contain_text('章节读取失败')
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.locator('#retry-load').click()
            expect(page.locator('#chapter-title')).to_have_text('第二章：切换目标')
            expect(page.locator('#load-error')).not_to_be_visible()
            history_responses = []
            old_history = page.request.get(url + '/api/chapters').json()[:1]
            def delay_history(route):
                if not history_responses:
                    history_responses.append(route)
                else:
                    route.continue_()
            page.route(url + '/api/chapters', delay_history)
            with page.expect_request(url + '/api/chapters'):
                page.evaluate('() => { window.oldHistoryRefresh = refreshHistory(); }')
            page.evaluate('refreshHistory()')
            expect(page.locator('#chapter-count')).to_have_text('2')
            history_responses[0].fulfill(json=old_history)
            page.evaluate('window.oldHistoryRefresh')
            expect(page.locator('#chapter-count')).to_have_text('2')
            page.unroute(url + '/api/chapters', delay_history)
            page.locator('[data-chapter="demo-network-layer"]').click()
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.goto(url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.evaluate("localStorage.setItem('zhizhang-last-chapter', 'missing-chapter')")
            page.goto(url)
            expect(page.locator('#chapter-title')).to_have_text('第二章：切换目标')
            page.goto(url + '/#chapter/demo-network-layer')
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            restricted = browser.new_page()
            restricted.on('pageerror', lambda error: errors.append(str(error)))
            restricted.add_init_script("Storage.prototype.getItem = function() {throw new Error('storage unavailable');}; Storage.prototype.setItem = function() {throw new Error('storage unavailable');};")
            restricted.goto(url)
            expect(restricted.locator('#chapter-title')).to_have_text('第二章：切换目标')
            restricted.close()
            assert not errors, errors
            browser.close()
        output = Path(__file__).resolve().parent.parent / 'test-results'
        output.mkdir(exist_ok=True)
        report = {'passed': ['chapter content loads while settings response is still pending', 'late startup settings cannot overwrite newer settings', 'startup list failures can retry without reloading', 'cancelled navigation ignores a late failure', 'failed chapter loading keeps current content and can retry', 'a late history response cannot replace the more recently loaded list', 'the home page resumes the last read chapter and explicit links still take priority', 'missing remembered chapters and unavailable browser storage fall back to a saved chapter'], 'first_content_with_settings_held_seconds': first_content, 'javascript_errors': errors, 'external_model_used': False}
        (output / 'startup-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
