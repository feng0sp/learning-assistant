"""Protect chapter-specific question drafts and delayed answers."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-questions-')
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
            def switch(chapter_id):
                page.locator(f'[data-chapter="{chapter_id}"]').click()
                page.wait_for_function('(id) => state.chapter.id === id', arg=chapter_id)

            original = 'demo-network-layer'
            page.locator('#question').fill('网络层的问题草稿')
            switch(next_id)
            expect(page.locator('#question')).to_have_value('')
            page.locator('#question').fill('另一章的问题草稿')
            switch(original)
            expect(page.locator('#question')).to_have_value('网络层的问题草稿')

            endpoint = url + '/api/chapters/' + original + '/questions'
            def delayed_answer(route):
                response = route.fetch()
                switch(next_id)
                expect(page.locator('#question')).to_have_value('另一章的问题草稿')
                switch(original)
                expect(page.locator('#messages .message.answer')).to_have_count(1)
                route.fulfill(response=response)

            page.route(endpoint, delayed_answer)
            page.locator('#ask-button').click()
            expect(page.locator('#ask-button')).to_be_enabled()
            expect(page.locator('#messages .message.answer')).to_have_count(1)
            expect(page.locator('#question')).to_have_value('')
            page.unroute(endpoint, delayed_answer)

            def edited_answer(route):
                response = route.fetch()
                page.locator('#question').fill('临时修改')
                page.locator('#question').fill('继续解释')
                route.fulfill(response=response)

            page.route(endpoint, edited_answer)
            page.locator('#question').fill('继续解释')
            page.locator('#ask-button').click()
            expect(page.locator('#ask-button')).to_be_enabled()
            expect(page.locator('#question')).to_have_value('继续解释')
            page.unroute(endpoint, edited_answer)

            def away_answer(route):
                response = route.fetch()
                switch(next_id)
                route.fulfill(response=response)

            page.route(endpoint, away_answer)
            page.locator('#ask-button').click()
            expect(page.locator('#ask-button')).to_be_enabled()
            expect(page.locator('#question')).to_have_value('另一章的问题草稿')
            switch(original)
            expect(page.locator('#question')).to_have_value('')
            page.unroute(endpoint, away_answer)

            page.route(endpoint, lambda route: route.fulfill(status=502, json={'detail': '测试失败响应'}))
            page.locator('#question').fill('失败后保留的问题')
            page.locator('#ask-button').click()
            expect(page.locator('#toast')).to_have_text('测试失败响应')
            expect(page.locator('#question')).to_have_value('失败后保留的问题')
            switch(next_id)
            switch(original)
            expect(page.locator('#question')).to_have_value('失败后保留的问题')
            assert not errors, errors
            browser.close()
        output = Path(__file__).resolve().parent.parent / 'test-results'
        output.mkdir(exist_ok=True)
        report = {'passed': ['chapter-specific drafts survive switching', 'delayed answers do not duplicate loaded messages', 'editing and restoring the submitted text keeps the new draft', 'answers received away clear only the submitted unchanged draft', 'failed requests preserve drafts'], 'javascript_errors': errors, 'external_model_used': False}
        (output / 'questions-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
