"""Verify bookmarked PDF selection and stale preview handling in Chromium."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-material-browser-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from tests.fake_provider import FakeProvider
from tests.pdf_fixtures import PAGE_TEXTS, bookmarked_pdf


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
    output = Path(__file__).resolve().parent.parent / 'test-results'
    output.mkdir(exist_ok=True)
    checked, errors = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.on('pageerror', lambda error: errors.append(str(error)))
            response = page.request.put(url + '/api/settings', data={'base_url': provider.base_url, 'model': 'test-only', 'api_key': ''})
            assert response.ok, response.text()
            page.goto(url)
            page.locator('#new-chapter').click()
            page.locator('#file-input').set_input_files({'name': '有目录的教材.pdf', 'mimeType': 'application/pdf', 'buffer': bookmarked_pdf(untrusted_title=True)})
            expect(page.locator('#outline-select option')).to_have_count(4)
            page.locator('#outline-select').select_option('2')
            page.locator('#apply-outline').click()
            expect(page.locator('#title-input')).to_have_value('1.2 分组转发')
            expect(page.locator('#page-start')).to_have_value('2')
            expect(page.locator('#page-end')).to_have_value('3')
            expect(page.locator('#preview-text')).to_have_text(PAGE_TEXTS[1])
            expect(page.locator('#outline-status')).to_contain_text('预览首尾页')
            checked.append('nested PDF bookmark fills the chapter title, inferred range and first-page preview')

            def delay_old_preview(route):
                response = route.fetch()
                page.locator('#preview-page').fill('3')
                page.locator('#preview-load').click()
                expect(page.locator('#preview-text')).to_have_text(PAGE_TEXTS[2])
                route.fulfill(response=response)

            page.route('**/api/materials/*/pages/1', delay_old_preview)
            page.locator('#preview-page').fill('1')
            page.evaluate('previewPage()')
            expect(page.locator('#preview-text')).to_have_text(PAGE_TEXTS[2])
            page.unroute('**/api/materials/*/pages/1', delay_old_preview)
            checked.append('a late response for an earlier preview cannot overwrite the latest selected page')

            page.locator('#outline-select').select_option('3')
            page.locator('#apply-outline').click()
            expect(page.locator('#title-input')).to_have_value('<img src=x onerror="window.__outlineInjected=1">')
            expect(page.locator('#outline-picker img')).to_have_count(0)
            assert page.evaluate('window.__outlineInjected === undefined')
            page.locator('#outline-select').select_option('2')
            page.locator('#apply-outline').click()
            expect(page.locator('#preview-text')).to_have_text(PAGE_TEXTS[1])
            page.reload()
            page.locator('#new-chapter').click()
            expect(page.locator('#outline-select')).to_have_value('2')
            expect(page.locator('#title-input')).to_have_value('1.2 分组转发')
            expect(page.locator('#page-start')).to_have_value('2')
            expect(page.locator('#page-end')).to_have_value('3')
            checked.append('untrusted bookmark titles stay plain text and the selected chapter draft survives reload')
            page.screenshot(path=str(output / 'materials-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('#chapter-dialog').evaluate('(element) => element.scrollWidth <= element.clientWidth')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'materials-mobile.png'))
            page.locator('#outline-picker').scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'materials-picker-mobile.png'))
            page.locator('#generate-button').click()
            expect(page.locator('#chapter-dialog')).not_to_be_visible(timeout=15000)
            expect(page.locator('#chapter-title')).to_have_text('1.2 分组转发', timeout=15000)
            chapters = page.request.get(url + '/api/chapters').json()
            chapter_id = next(item['id'] for item in chapters if item['title'] == '1.2 分组转发')
            chapter = page.request.get(url + '/api/chapters/' + chapter_id).json()
            source_text = '\n'.join(source['text'] for source in chapter['sources'])
            assert PAGE_TEXTS[1] in source_text and PAGE_TEXTS[2] in source_text
            assert PAGE_TEXTS[0] not in source_text and PAGE_TEXTS[3] not in source_text
            checked.append('mobile generation sends and saves only the two selected PDF pages')

            page.locator('#new-chapter').click()
            page.locator('#file-input').set_input_files({'name': '乱序目录.pdf', 'mimeType': 'application/pdf', 'buffer': bookmarked_pdf(irregular=True)})
            expect(page.locator('#outline-select option')).to_have_count(2)
            page.locator('#apply-outline').click()
            expect(page.locator('#page-start')).to_have_value('4')
            expect(page.locator('#page-end')).to_have_value('4')
            expect(page.locator('#outline-status')).to_contain_text('手动确认并调整结束页')
            page.locator('#file-input').set_input_files({'name': '课堂笔记.txt', 'mimeType': 'text/plain', 'buffer': '课堂笔记：路由器根据目的地址选择下一跳，网络层负责跨网络传递分组。'.encode('utf-8')})
            expect(page.locator('#material-name')).to_have_text('课堂笔记.txt')
            expect(page.locator('#outline-picker')).not_to_be_visible()
            page.locator('#remove-material').click()
            expect(page.locator('#material-preview')).not_to_be_visible()
            checked.append('irregular bookmarks require a manual end page; replacing or removing a PDF clears its picker')
            original_file = {'name': '原有资料.txt', 'mimeType': 'text/plain', 'buffer': '网络层根据目的地址转发分组，这份原有资料用于检查替换期间的操作。'.encode('utf-8')}
            page.locator('#file-input').set_input_files(original_file)
            expect(page.locator('#file-input')).to_be_enabled()
            expect(page.locator('#material-name')).to_have_text('原有资料.txt')
            replacement_file = {'name': '替换资料.txt', 'mimeType': 'text/plain', 'buffer': '这份替换资料在用户移除操作之后返回，不应该重新出现在章节表单里。'.encode('utf-8')}
            replacement = page.request.post(url + '/api/materials', multipart={'file': replacement_file})
            assert replacement.ok

            def remove_during_upload(route):
                page.locator('#remove-material').click()
                route.fulfill(status=200, content_type='application/json', body=replacement.text())

            page.route(url + '/api/materials', remove_during_upload)
            page.locator('#file-input').set_input_files(replacement_file)
            page.wait_for_function('() => !state.uploading')
            expect(page.locator('#material-preview')).not_to_be_visible()
            assert page.evaluate('state.material === null')
            page.unroute(url + '/api/materials', remove_during_upload)
            checked.append('removing a material during replacement prevents the late upload from restoring it')

            page.locator('#file-input').set_input_files(original_file)
            expect(page.locator('#file-input')).to_be_enabled()
            expect(page.locator('#material-name')).to_have_text('原有资料.txt')
            nearly_full = '甲' * 89990
            page.locator('#text-input').fill(nearly_full)
            page.locator('#material-preview details').evaluate('(element) => element.open = true')
            page.locator('#edit-extracted').click()
            expect(page.locator('#generation-error')).to_contain_text('9 万')
            expect(page.locator('#text-input')).to_have_value(nearly_full)
            expect(page.locator('#material-preview')).to_be_visible()
            page.locator('#text-input').fill('已有的补充笔记。')
            page.locator('#edit-extracted').click()
            expect(page.locator('#material-preview')).not_to_be_visible()
            assert '网络层根据目的地址' in page.locator('#text-input').input_value()
            checked.append('editing extracted text keeps both inputs intact at the length limit and succeeds after shortening')
            page.locator('#file-input').set_input_files(original_file)
            expect(page.locator('#file-input')).to_be_enabled()
            expect(page.locator('#material-name')).to_have_text('原有资料.txt')

            def outdated_edit_error(route):
                page.locator('#remove-material').click()
                page.locator('#text-input').fill('')
                page.locator('#generate-button').click()
                route.fulfill(status=422, content_type='application/json', body=json.dumps({'detail': '旧资料读取失败'}))

            page.route('**/api/materials/*/pages/1', outdated_edit_error)
            page.evaluate('editExtractedPage()')
            expect(page.locator('#generation-error')).to_contain_text('请上传资料')
            assert '旧资料读取失败' not in page.locator('#generation-error').inner_text()
            page.unroute('**/api/materials/*/pages/1', outdated_edit_error)
            checked.append('an obsolete extraction error cannot replace feedback for a newer operation')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False}
        (output / 'materials-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        provider.close()
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
