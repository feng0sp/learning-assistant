"""Protect model settings drafts during loading and saving."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-settings-race-')
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
            page.goto(url)
            expect(page.locator('#settings-state')).to_have_text('已配置')
            held = []
            endpoint = url + '/api/settings'
            page.route(endpoint, lambda route: held.append(route))
            page.evaluate('() => { window.settingsOpenPending = Promise.all([openSettings(), openSettings()]); }')
            page.wait_for_timeout(150)
            assert len(held) == 1, f'Duplicate settings requests: {len(held)}'
            held[0].fulfill(response=held[0].fetch())
            page.evaluate('window.settingsOpenPending')
            expect(page.locator('#settings-dialog')).to_be_visible()
            page.locator('#model-name').fill('unsaved-model-name')
            page.evaluate('openSettings()')
            expect(page.locator('#model-name')).to_have_value('unsaved-model-name')
            assert len(held) == 1
            page.unroute(endpoint)

            pending = []
            def hold_save(route):
                if route.request.method == 'PUT':
                    pending.append((route, route.fetch()))
                else:
                    route.continue_()
            page.route(endpoint, hold_save)
            page.locator('#api-key').fill('first-test-key')
            page.locator('#save-settings').click()
            expect(page.locator('#save-settings')).to_be_disabled()
            page.locator('#api-key').fill('second-unsaved-test-key')
            page.locator('#model-name').fill('second-unsaved-model')
            pending[0][0].fulfill(response=pending[0][1])
            expect(page.locator('#save-settings')).to_be_enabled()
            expect(page.locator('#api-key')).to_have_value('second-unsaved-test-key')
            expect(page.locator('#model-name')).to_have_value('second-unsaved-model')
            expect(page.locator('#settings-success')).to_contain_text('尚未保存')
            saved = page.request.get(endpoint).json()
            assert saved['model'] == 'unsaved-model-name'
            assert 'first-test-key' not in json.dumps(saved) and 'second-unsaved-test-key' not in json.dumps(saved)
            page.unroute(endpoint, hold_save)
            page.locator('#save-settings').click()
            expect(page.locator('#settings-success')).to_contain_text('设置已保存')
            expect(page.locator('#api-key')).to_have_value('')
            assert page.request.get(endpoint).json()['model'] == 'second-unsaved-model'
            page.locator('[data-close="settings-dialog"]').click()
            page.route(endpoint, lambda route: route.fulfill(status=503, json={'detail': '设置暂时无法读取'}))
            page.locator('#open-settings').click()
            expect(page.locator('#toast')).to_have_text('设置暂时无法读取')
            page.unroute(endpoint)
            page.locator('#open-settings').click()
            expect(page.locator('#settings-dialog')).to_be_visible()
            page.route(endpoint, lambda route: route.fulfill(status=503, json={'detail': '设置暂时无法保存'}))
            page.locator('#api-key').fill('retry-only-test-key')
            page.locator('#save-settings').click()
            expect(page.locator('#settings-error')).to_have_text('设置暂时无法保存')
            expect(page.locator('#save-settings')).to_be_enabled()
            expect(page.locator('#api-key')).to_have_value('retry-only-test-key')
            page.unroute(endpoint)
            assert not errors, errors
            browser.close()
        output = Path(__file__).resolve().parent.parent / 'test-results'
        output.mkdir(exist_ok=True)
        report = {'passed': ['duplicate settings opens share one read and do not reset an open form', 'saving earlier settings keeps new key and model edits with an unsaved notice', 'a subsequent save persists the latest model and clears the submitted key', 'failed reads can reopen and failed saves keep the unsaved key'], 'javascript_errors': errors, 'external_model_used': False}
        (output / 'settings-race-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
