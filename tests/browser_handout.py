"""Print an actual chapter to PDF and verify controls, persistence and escaping."""
import io
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-handout-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

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
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            note = '打印之前的新笔记：转发决定下一跳，路由选择决定路径。'
            page.locator('#notes').fill(note)
            with page.expect_popup() as popup:
                page.locator('#print-chapter').click()
            handout = popup.value
            handout.on('pageerror', lambda error: errors.append(str(error)))
            handout.wait_for_url('**/api/chapters/demo-network-layer/print')
            expect(handout.locator('.personal-notes')).to_contain_text(note)
            assert handout.evaluate('window.opener === null')
            expect(handout.locator('h1')).to_contain_text('网络层')
            expect(handout.locator('.source-appendix')).not_to_be_visible()
            handout.evaluate('window.print = () => { window.__printRequested = true; }')
            handout.locator('#print-handout').click()
            assert handout.evaluate('window.__printRequested === true')
            checked.append('print opens a separate chapter snapshot after saving pending notes; the button invokes browser printing')

            handout.locator('#include-sources').check()
            handout.evaluate('document.fonts.ready')
            handout.emulate_media(media='print')
            expect(handout.locator('.print-controls')).not_to_be_visible()
            full_pdf = handout.pdf(path=str(output / 'handout-full.pdf'), prefer_css_page_size=True, print_background=True)
            reader = PdfReader(io.BytesIO(full_pdf))
            page_texts = [pdf_page.extract_text() for pdf_page in reader.pages]
            combined = '\n'.join(page_texts)
            for heading in ('核心知识点', '原创例题', '生活类比', '自测练习', '自测答案与解析', '资料来源原文', note):
                assert heading in combined, heading
            questions_page = next(index for index, value in enumerate(page_texts) if '自测练习' in value)
            answers_page = next(index for index, value in enumerate(page_texts) if '自测答案与解析' in value)
            assert answers_page > questions_page
            assert '正确答案：' not in page_texts[questions_page]
            assert all(value.strip() for value in page_texts), 'unexpected blank PDF page'
            for index, value in enumerate(page_texts, 1):
                assert f'{index} / {len(page_texts)}' in value, 'missing printed page number'
            checked.append('actual A4 PDF contains Chinese learning content and sources; quiz answers begin on a later page')

            handout.emulate_media(media='screen')
            handout.locator('#include-solutions').uncheck()
            handout.locator('#include-notes').uncheck()
            handout.locator('#include-sources').uncheck()
            expect(handout.locator('.answer-key')).not_to_be_visible()
            expect(handout.locator('.answer-space').first).to_be_visible()
            hidden_pdf = handout.pdf(prefer_css_page_size=True)
            hidden_text = '\n'.join(item.extract_text() for item in PdfReader(io.BytesIO(hidden_pdf)).pages)
            assert '自测答案与解析' not in hidden_text and '资料来源原文' not in hidden_text and note not in hidden_text
            assert '自测练习' in hidden_text and '原创例题' in hidden_text
            checked.append('answer, notes and source switches affect the exported PDF rather than only the screen')

            handout.set_viewport_size({'width': 390, 'height': 844})
            assert handout.evaluate('document.documentElement.scrollWidth <= innerWidth')
            handout.screenshot(path=str(output / 'handout-mobile.png'))
            payload = page.request.get(url + '/api/backups/export').json()
            payload['chapters'][0]['id'] = 'handout-untrusted'
            payload['chapters'][0]['title'] = '<img src=x onerror="window.__handoutInjected=1">'
            payload['chapters'][0]['guide']['overview'] = '<script>window.__handoutInjected=1</script>'
            payload['chapters'][0]['sources'][0]['text'] = '<svg onload="window.__handoutInjected=1">教材原文</svg>'
            response = page.request.post(url + '/api/backups/import', multipart={'file': {'name': 'fixture.json', 'mimeType': 'application/json', 'buffer': json.dumps(payload).encode('utf-8')}})
            assert response.ok, response.text()
            handout.goto(url + '/api/chapters/handout-untrusted/print')
            handout.locator('#include-sources').check()
            expect(handout.locator('main img,main script,main svg')).to_have_count(0)
            expect(handout.locator('h1')).to_contain_text('<img')
            expect(handout.locator('.source-appendix')).to_contain_text('<svg')
            assert handout.evaluate('window.__handoutInjected === undefined')
            assert handout.evaluate('document.documentElement.scrollWidth <= innerWidth')
            checked.append('handout controls fit a mobile screen and imported HTML remains literal text')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'pdf_pages': len(reader.pages), 'javascript_errors': errors, 'external_model_used': False}
        (output / 'handout-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
