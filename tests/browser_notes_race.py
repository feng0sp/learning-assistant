"""Protect edits made while chapter navigation and note saving are in flight."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-notes-race-')
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
            response = page.request.post(url + '/api/chapters/generate', data={'course': '计算机网络', 'chapter': '准备切换到的章节', 'mode': 'topic'})
            job_id = response.json()['job_id']
            for _ in range(100):
                job = page.request.get(url + '/api/jobs/' + job_id).json()
                if job['status'] in {'completed', 'failed'}:
                    break
                time.sleep(.1)
            assert job['status'] == 'completed', job
            next_id = job['chapter_id']
            page.goto(url + '/#chapter/demo-network-layer')
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            final_text = '读取另一章时仍在输入；保存响应回来前又补上了最后一句。'
            injected = []

            def load_next(route):
                response = route.fetch()
                page.locator('#notes').fill('读取另一章时仍在输入。')
                injected.append('chapter request')
                route.fulfill(response=response)

            def save_current(route):
                response = route.fetch()
                if 'note request' not in injected:
                    page.locator('#notes').fill(final_text)
                    injected.append('note request')
                route.fulfill(response=response)

            page.route(url + '/api/chapters/' + next_id + '/study', load_next)
            page.route(url + '/api/chapters/demo-network-layer/notes', save_current)
            page.locator(f'[data-chapter="{next_id}"]').click()
            expect(page.locator('#chapter-title')).to_have_text('准备切换到的章节')
            assert injected == ['chapter request', 'note request'], injected
            saved = page.request.get(url + '/api/chapters/demo-network-layer').json()['notes']
            assert saved == final_text, f'Late edit was lost: {saved!r}'
            page.locator('[data-chapter="demo-network-layer"]').click()
            expect(page.locator('#notes')).to_have_value(final_text)
            page.unroute(url + '/api/chapters/' + next_id + '/study', load_next)
            page.unroute(url + '/api/chapters/demo-network-layer/notes', save_current)

            # An old response must not mark notes clean while a newer write is queued.
            # The text returns to A while a queued B write is still pending.
            latest_text = '排队保存后又修改：这一份才是最终笔记。'
            writes = []

            def overlapping_save(route):
                writes.append(route.request.post_data_json['notes'])
                response = route.fetch()
                if len(writes) == 1:
                    page.locator('#notes').fill('正在排队的中间版本。')
                    page.locator('#save-notes').click()
                    page.locator('#notes').fill(latest_text)
                elif len(writes) == 2:
                    page.locator(f'[data-chapter="{next_id}"]').click()
                route.fulfill(response=response)

            page.route(url + '/api/chapters/demo-network-layer/notes', overlapping_save)
            page.locator('#notes').fill(latest_text)
            page.locator('#save-notes').click()
            expect(page.locator('#chapter-title')).to_have_text('准备切换到的章节')
            saved = page.request.get(url + '/api/chapters/demo-network-layer').json()['notes']
            assert saved == latest_text, f'Queued write replaced the final edit: {saved!r}'
            assert writes == [latest_text, '正在排队的中间版本。', latest_text], writes
            page.locator('[data-chapter="demo-network-layer"]').click()
            expect(page.locator('#notes')).to_have_value(latest_text)
            page.unroute(url + '/api/chapters/demo-network-layer/notes', overlapping_save)

            def stay_on_current(route):
                response = route.fetch()
                page.locator('[data-chapter="demo-network-layer"]').click()
                route.fulfill(response=response)

            page.route(url + '/api/chapters/demo-network-layer/notes', stay_on_current)
            page.locator('#notes').fill('开始切换后，又点击了当前章节，希望留在这里。')
            # Await the navigation function so the assertion runs after all of its work.
            page.evaluate('(id) => openChapter(id)', next_id)
            expect(page.locator('#export-link')).to_have_attribute('href', '/api/chapters/demo-network-layer/export')
            expect(page.locator('#notes')).to_have_value('开始切换后，又点击了当前章节，希望留在这里。')
            page.unroute(url + '/api/chapters/demo-network-layer/notes', stay_on_current)

            export_text = '点击导出之后、保存响应之前补写的最后一句。'
            export_writes = []

            def late_export_edit(route):
                response = route.fetch()
                export_writes.append(route.request.post_data_json['notes'])
                if len(export_writes) == 1:
                    page.locator('#notes').fill(export_text)
                route.fulfill(response=response)

            page.route(url + '/api/chapters/demo-network-layer/notes', late_export_edit)
            page.locator('#notes').fill('点击导出时的初稿。')
            with page.expect_download() as download:
                page.locator('#export-link').click()
            exported_path = Path(TEST_DIRECTORY.name) / 'latest-notes.md'
            download.value.save_as(str(exported_path))
            assert export_text in exported_path.read_text(encoding='utf-8'), 'Markdown export missed the final note edit'
            page.unroute(url + '/api/chapters/demo-network-layer/notes', late_export_edit)
            assert not errors, errors
            browser.close()
        output = Path(__file__).resolve().parent.parent / 'test-results'
        output.mkdir(exist_ok=True)
        report = {'passed': ['edits during chapter loading and the final note-save response survive navigation', 'an earlier save response cannot mark later queued edits as clean', 'the most recent chapter selection wins while note saving is pending', 'Markdown export includes edits made before the final save response arrives'], 'javascript_errors': errors, 'external_model_used': False}
        (output / 'notes-race-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
