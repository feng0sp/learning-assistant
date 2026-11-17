"""Verify portable backups and recovery after a lost import response in a real browser."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-backup-browser-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app


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
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.on('pageerror', lambda error: errors.append(str(error)))
            # No request is made to this reserved example endpoint.
            page.request.put(url + '/api/settings', data={'base_url': 'https://example.invalid/v1', 'model': 'unused', 'api_key': 'backup-browser-secret'})
            page.goto(url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.get_by_role('tab', name='自测练习').click()
            page.locator('input[name="quiz-0"][value="1"]').check()
            page.locator('[data-check="0"]').click()
            expect(page.locator('#quiz-0 .quiz-feedback')).to_be_visible()
            old_note = '备份时的原始笔记。\n这一行也要保留。'
            page.locator('#notes').fill(old_note)
            page.locator('#open-data').click()
            with page.expect_download() as download:
                page.locator('#backup-export').click()
            destination = output / 'backup-download.json'
            download.value.save_as(str(destination))
            payload = json.loads(destination.read_text(encoding='utf-8'))
            assert payload['chapters'][0]['notes'] == old_note
            assert payload['chapters'][0]['attempts'][0]['correct'] is False
            assert 'backup-browser-secret' not in destination.read_text(encoding='utf-8')
            checked.append('export saves pending notes and downloads learning data without model settings')
            page.locator('[data-close="data-dialog"]').click()
            current_note = '备份之后新学到的内容，导入时应保留。'
            page.locator('#notes').fill(current_note)
            page.locator('#save-notes').click()
            expect(page.locator('#notes-status')).to_have_text('已保存到本机')
            page.locator('#open-data').click()
            page.locator('#backup-file').set_input_files({'name': 'invalid.json', 'mimeType': 'application/json', 'buffer': b'{"application":"wrong"}'})
            expect(page.locator('#backup-error')).to_contain_text('备份格式不正确')
            expect(page.locator('#backup-import')).to_be_disabled()
            assert len(page.request.get(url + '/api/chapters').json()) == 1
            checked.append('invalid backups show a recoverable error without adding records')
            page.locator('#backup-file').set_input_files(str(destination))
            expect(page.locator('#backup-preview-summary')).to_have_text('共 1 章：新增 0 章，冲突副本 1 章，跳过 0 章。')
            expect(page.locator('#backup-import')).to_be_enabled()
            page.screenshot(path=str(output / 'backup-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('#data-dialog').evaluate('(element) => element.scrollWidth <= element.clientWidth')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'backup-mobile.png'))

            def lose_response(route):
                # Interception can omit uploaded file bytes. Relay this exact selected
                # fixture explicitly, commit it, then drop the browser's response.
                response = page.request.post(route.request.url, headers={'Origin': url}, multipart={
                    'file': {'name': destination.name, 'mimeType': 'application/json', 'buffer': destination.read_bytes()},
                })
                if response.status != 200:
                    route.fulfill(response=response)
                    raise AssertionError(f'Import interception failed: HTTP {response.status}: {response.text()[:600]}')
                route.abort('failed')

            page.route(url + '/api/backups/import', lose_response)
            page.locator('#backup-import').click()
            expect(page.locator('#backup-error')).to_contain_text('无法连接本地服务')
            assert len(page.request.get(url + '/api/chapters').json()) == 2
            page.unroute(url + '/api/backups/import', lose_response)
            page.locator('#backup-import').click()
            expect(page.locator('#backup-success')).to_contain_text('跳过 1 章')
            assert len(page.request.get(url + '/api/chapters').json()) == 2
            original = page.request.get(url + '/api/chapters/demo-network-layer').json()
            assert original['notes'] == current_note
            assert page.request.get(url + '/api/settings').json()['has_key']
            checked.append('a lost response can be retried without duplicate imports or overwriting newer notes')
            page.locator('#backup-file').set_input_files(str(destination))
            expect(page.locator('#backup-preview-summary')).to_contain_text('冲突副本 0 章，跳过 1 章')
            expect(page.locator('#backup-import')).to_be_disabled()
            page.locator('[data-close="data-dialog"]').click()
            page.locator('#toggle-navigation').click()
            page.locator('.chapter-nav').filter(has_text='导入副本').click()
            expect(page.locator('#notes')).to_have_value(old_note)
            page.get_by_role('tab', name='自测练习').click()
            expect(page.locator('#quiz-0 .quiz-feedback')).to_contain_text('正确答案是 A')
            page.reload()
            expect(page.locator('#notes')).to_have_value(old_note)
            checked.append('imported copies retain notes and quiz results after reload; repeat preview skips them')

            payload['chapters'][0]['id'] = 'untrusted-backup-title'
            payload['chapters'][0]['title'] = '<img src=x onerror="window.__backupInjected=1">'
            page.locator('#toggle-navigation').click()
            page.locator('#open-data').click()
            page.locator('#backup-file').set_input_files({'name': 'title.json', 'mimeType': 'application/json', 'buffer': json.dumps(payload).encode('utf-8')})
            expect(page.locator('#backup-preview')).to_be_visible()
            expect(page.locator('#backup-preview img')).to_have_count(0)
            page.locator('#backup-import').click()
            expect(page.locator('#backup-success')).to_contain_text('已导入 1 章')
            expect(page.locator('#chapter-list img')).to_have_count(0)
            assert page.evaluate('window.__backupInjected === undefined')
            checked.append('backup preview and imported metadata render untrusted text safely on mobile')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False}
        (output / 'backup-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
