"""Completed generation remains reachable after list errors or note conflicts."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-job-recovery-')
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
            page.route(url + '/api/chapters', lambda route: route.fulfill(status=503, json={'detail': '测试列表读取失败'}))
            page.evaluate('(id) => {state.job = id; return pollJob();}', job_id)
            expect(page.locator('#open-generated-chapter')).to_be_visible()
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.unroute(url + '/api/chapters')
            page.locator('#open-generated-chapter').click()
            expect(page.locator('#chapter-title')).to_have_text('准备切换到的章节')
            expect(page.locator('#job-banner')).not_to_be_visible()

            page.locator('[data-chapter="demo-network-layer"]').click()
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            response = page.request.put(url + '/api/chapters/demo-network-layer/notes', data={'notes': '另一窗口的新笔记', 'expected_revision': 0})
            assert response.ok
            page.locator('#notes').fill('当前窗口的未保存理解')
            page.locator('#save-notes').click()
            expect(page.locator('#merge-notes')).to_be_visible()
            page.evaluate('(id) => {state.job = id; return pollJob();}', job_id)
            expect(page.locator('#open-generated-chapter')).to_be_visible()
            expect(page.locator('#notes')).to_have_value('当前窗口的未保存理解')
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.locator('#merge-notes').click()
            expect(page.locator('#notes-status')).to_have_text('已保存到本机')
            page.locator('#open-generated-chapter').click()
            expect(page.locator('#chapter-title')).to_have_text('准备切换到的章节')
            saved = page.request.get(url + '/api/chapters/demo-network-layer').json()['notes']
            assert '另一窗口的新笔记' in saved and '当前窗口的未保存理解' in saved

            def newer_navigation(route):
                response = route.fetch()
                page.locator('[data-chapter="demo-network-layer"]').click()
                expect(page.locator('#chapter-title')).to_contain_text('网络层')
                route.fulfill(response=response)
            page.route(url + '/api/chapters', newer_navigation)
            page.evaluate('(id) => {state.job = id; return pollJob();}', job_id)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            expect(page.locator('#open-generated-chapter')).to_be_visible()
            page.unroute(url + '/api/chapters', newer_navigation)
            page.set_viewport_size({'width': 320, 'height': 760})
            page.evaluate("document.documentElement.style.fontSize = '200%'")
            expect(page.locator('#open-generated-chapter')).to_be_in_viewport()
            geometry = page.locator('#job-banner').evaluate('''banner => ({
                width: banner.clientWidth, scrollWidth: banner.scrollWidth,
                pageWidth: document.documentElement.clientWidth,
                pageScrollWidth: document.documentElement.scrollWidth
            })''')
            assert geometry['scrollWidth'] <= geometry['width'] + 1, geometry
            assert geometry['pageScrollWidth'] <= geometry['pageWidth'] + 1, geometry
            assert page.evaluate('''() => {
                const range = document.createRange();
                range.selectNodeContents(document.querySelector('.brand > span:last-child').firstChild);
                return range.getClientRects().length;
            }''') == 1, 'Brand name must stay on one line at 320px with doubled text'
            page.evaluate("showToast('这一章已整理好，开始学习吧。')")
            toast = page.locator('#toast').bounding_box()
            assert toast['width'] >= 280 and toast['height'] < 200, toast
            screenshot = Path(__file__).resolve().parent.parent / 'test-results' / 'job-recovery-mobile.png'
            screenshot.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshot))
            page.locator('#open-generated-chapter').click()
            expect(page.locator('#chapter-title')).to_have_text('准备切换到的章节')
            expect(page.locator('#job-banner')).not_to_be_visible()
            assert not errors, errors
            browser.close()
        output = Path(__file__).resolve().parent.parent / 'test-results'
        output.mkdir(exist_ok=True)
        report = {'passed': ['completed jobs offer an open action after a temporary list failure', 'note conflicts keep edits and allow opening the completed chapter after merging', 'manual navigation while the completion list is loading takes precedence', 'completed chapter action stays visible and usable at 320 pixels with double text size'], 'javascript_errors': errors, 'external_model_used': False}
        (output / 'job-recovery-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
