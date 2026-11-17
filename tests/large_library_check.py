"""Exercise the documented 1000-chapter backup limit using isolated local data."""
import copy
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-library-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import httpx
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
    metrics, errors, checked = {}, [], []
    try:
        with httpx.Client(base_url=url, timeout=120, trust_env=False) as client:
            document = client.get('/api/backups/export').json()
            template = document['chapters'][0]
            for index in range(1, 1000):
                chapter = copy.deepcopy(template)
                chapter['id'] = f'library-{index:04d}'
                chapter['course'] = f'测试课程 {index // 20:02d}'
                chapter['title'] = f'规模检查章节 {index:04d}'
                document['chapters'].append(chapter)
            payload = json.dumps(document, ensure_ascii=False).encode('utf-8')
            metrics['backup_bytes'] = len(payload)
            began = time.monotonic()
            imported = client.post('/api/backups/import', files={'file': ('library.json', payload, 'application/json')})
            imported.raise_for_status()
            assert imported.json()['imported'] == 999 and imported.json()['skipped'] == 1
            metrics['import_seconds'] = round(time.monotonic() - began, 3)
            assert len(client.get('/api/chapters').json()) == 1000
            repeated = client.post('/api/backups/import', files={'file': ('library.json', payload, 'application/json')})
            repeated.raise_for_status()
            assert repeated.json()['imported'] == 0 and repeated.json()['skipped'] == 1000
            checked.append('the documented 1000-chapter backup imports completely and duplicate import adds nothing')

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.on('pageerror', lambda error: errors.append(str(error)))
                began = time.monotonic()
                page.goto(url + '/#chapter/demo-network-layer')
                expect(page.locator('#chapter-count')).to_have_text('1000')
                expect(page.locator('.chapter-nav')).to_have_count(50)
                expect(page.locator('#history-page-label')).to_have_text('第 1 / 20 页')
                metrics['open_library_seconds'] = round(time.monotonic() - began, 3)
                metrics['initial_chapter_buttons'] = page.locator('.chapter-nav').count()
                page.locator('#history-next').click()
                expect(page.locator('#history-page-label')).to_have_text('第 2 / 20 页')
                expect(page.locator('.chapter-nav')).to_have_count(50)
                page.locator('#history-prev').click()
                expect(page.locator('#history-page-label')).to_have_text('第 1 / 20 页')
                began = time.monotonic()
                page.locator('#chapter-search').fill('规模检查 0999')
                expect(page.locator('.chapter-nav')).to_have_count(1)
                expect(page.locator('#history-pages')).not_to_be_visible()
                metrics['filter_seconds'] = round(time.monotonic() - began, 3)
                page.locator('.chapter-nav').click()
                expect(page.locator('#chapter-title')).to_have_text('规模检查章节 0999')
                note = '千章记录中的最后一章，搜索后保存的笔记。'
                page.locator('#notes').fill(note)
                page.locator('#save-notes').click()
                expect(page.locator('#notes-status')).to_have_text('已保存到本机')
                checked.append('a 1000-chapter browser library can filter, open and save the last chapter')
                page.locator('#chapter-search').fill('')
                expect(page.locator('.chapter-nav')).to_have_count(50)
                page.reload()
                expect(page.locator('#chapter-title')).to_have_text('规模检查章节 0999')
                expect(page.locator('[data-chapter="library-0999"]')).to_have_attribute('aria-current', 'page')
                expect(page.locator('#history-page-label')).to_have_text('第 20 / 20 页')
                checked.append('chapter navigation renders 50 rows per page and reveals a deep-linked chapter on its page')
                browser.close()

            began = time.monotonic()
            exported = client.get('/api/backups/export')
            exported.raise_for_status()
            snapshot = exported.json()
            metrics['export_seconds'] = round(time.monotonic() - began, 3)
            assert len(snapshot['chapters']) == 1000
            assert next(item for item in snapshot['chapters'] if item['id'] == 'library-0999')['notes'] == note
            checked.append('export contains all 1000 chapters including the last saved note')
            extra = copy.deepcopy(template)
            extra['id'] = 'one-chapter-too-many'
            document['chapters'].append(extra)
            too_many = client.post('/api/backups/import', files={'file': ('too-many.json', json.dumps(document).encode('utf-8'), 'application/json')})
            assert too_many.status_code == 422
            assert len(client.get('/api/chapters').json()) == 1000
            checked.append('a 1001-chapter file fails before adding any records')
            assert not errors, errors
        report = {'passed': checked, 'metrics': metrics, 'javascript_errors': errors, 'external_model_used': False}
        (output / 'large-library-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
