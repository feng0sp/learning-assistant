"""Large conversations and paged review must stay responsive and preserve data."""
import argparse
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-density-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from backend.sample import DEMO_GUIDE
from backend.storage import db, dumps, now


def run(cpu_rate=1):
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
        with db() as conn:
            sources = conn.execute('SELECT sources FROM chapters WHERE id=?', ('demo-network-layer',)).fetchone()['sources']
            for index in range(31):
                chapter_id = f'dense-{index:02d}'
                conn.execute('INSERT INTO chapters (id,course,textbook,title,mode,goal,guide,sources,created_at) VALUES (?,?,?,?,?,?,?,?,?)', (chapter_id, '测试课程', '', f'密集记录 {index:02d}', 'demo', 'understand', dumps(DEMO_GUIDE), sources, now()))
                conn.execute('INSERT INTO attempts (chapter_id,question_index,choice,correct,created_at) VALUES (?,?,?,?,?)', (chapter_id, 0, 1, 0, now()))
            conn.executemany('INSERT INTO messages (chapter_id,question,answer,created_at) VALUES (?,?,?,?)', [('demo-network-layer', f'问题 {index:04d}', f'回答 {index:04d}：' + '这是已保存的详细讲解。' * 30, now()) for index in range(2000)])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.on('pageerror', lambda error: errors.append(str(error)))
            if cpu_rate != 1:
                session = page.context.new_cdp_session(page)
                session.send('Emulation.setCPUThrottlingRate', {'rate': cpu_rate})
            began = time.monotonic()
            with page.expect_response(lambda response: response.url.endswith('/api/chapters/demo-network-layer/study')) as study_response:
                page.goto(url + '/#chapter/demo-network-layer')
            expect(page.locator('#messages .answer')).to_have_count(30)
            metrics = {'simulated_cpu_slowdown': cpu_rate, 'open_2000_message_chapter_seconds': round(time.monotonic() - began, 3), 'initial_message_nodes': page.locator('#messages .message').count()}
            metrics['initial_study_bytes'] = len(study_response.value.body())
            expect(page.locator('#messages .question').first).to_contain_text('问题 1970')
            expect(page.locator('#messages .question').last).to_contain_text('问题 1999')
            page.locator('#older-messages').scroll_into_view_if_needed()
            page.locator('#messages').evaluate('(element) => {element.scrollTop = 0;}')
            before = page.locator('#messages .question').first.bounding_box()['y']
            page.locator('#older-messages').click()
            expect(page.locator('#messages .answer')).to_have_count(60)
            after = page.locator('#messages .question').filter(has_text='问题 1970').bounding_box()['y']
            assert abs(after - before) < 3, (before, after)
            full = page.request.get(url + '/api/chapters/demo-network-layer')
            assert len(full.json()['messages']) == 2000
            metrics['full_detail_bytes'] = len(full.body())
            assert metrics['initial_study_bytes'] < metrics['full_detail_bytes'] * .05
            checked.append('2000 saved answers initially render only 30 pairs and older answers preserve the reading position')
            page.reload()
            expect(page.locator('#messages .answer')).to_have_count(30)
            response = page.request.get(url + '/api/backups/export')
            assert response.ok, response.text()
            exported = response.json()
            messages = next(item for item in exported['chapters'] if item['id'] == 'demo-network-layer')['messages']
            assert len(messages) == 2000 and messages[0]['question'] == '问题 0000' and messages[-1]['question'] == '问题 1999'
            checked.append('reload bounds the conversation again while JSON backup keeps all messages')

            page.locator('#open-review').click()
            expect(page.locator('.review-card')).to_have_count(10)
            expect(page.locator('#review-page-label')).to_have_text('第 1 / 4 页')
            page.locator('input[name="review-0"][value="0"]').check()
            page.locator('[data-review-check="0"]').click()
            expect(page.locator('#review-0 .quiz-feedback')).to_contain_text('已答对')
            page.locator('#review-next').click()
            expect(page.locator('#review-page-label')).to_have_text('第 2 / 4 页')
            page.locator('input[name="review-10"][value="2"]').check()
            page.locator('#review-prev').click()
            expect(page.locator('#review-0 .quiz-feedback')).to_contain_text('已答对')
            page.locator('#review-next').click()
            expect(page.locator('input[name="review-10"][value="2"]')).to_be_checked()
            page.locator('#review-next').click()
            page.locator('#review-next').click()
            expect(page.locator('.review-card')).to_have_count(1)
            expect(page.locator('#review-next')).to_be_disabled()
            page.locator('[data-review-open="30"]').click()
            expect(page.locator('#review-dialog')).not_to_be_visible()
            expect(page.locator('#tab-quiz')).to_have_attribute('aria-selected', 'true')
            expect(page.locator('#chapter-title')).to_contain_text('密集记录')
            checked.append('31 review questions page by ten, keep drafts and results, and navigate from the last page')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'metrics': metrics, 'javascript_errors': errors, 'external_model_used': False}
        report_name = 'density-report.json' if cpu_rate == 1 else f'density-cpu{cpu_rate}-report.json'
        (output / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cpu-rate', type=int, choices=(1, 4, 6), default=1)
    run(parser.parse_args().cpu_rate)
