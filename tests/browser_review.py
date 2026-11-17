"""Chapter search and cross-chapter wrong-answer review, without a model service."""
import copy
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-review-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from backend.sample import DEMO_GUIDE
from backend.storage import db, dumps, now


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
        guide = copy.deepcopy(DEMO_GUIDE)
        for point in guide['knowledge_points']:
            point['source_ids'] = []
        guide['quiz'][2]['question'] = '<img src=x onerror="window.__reviewInjected=1"> IP 的交付有什么保证？'
        with db() as conn:
            conn.execute('INSERT INTO chapters (id,course,textbook,title,mode,goal,guide,sources,created_at,completed) VALUES (?,?,?,?,?,?,?,?,?,?)', (
                'review-other', '实验课程', 'Example Book Alpha', '传输层 <img src=x onerror="window.__reviewInjected=1">', 'topic', 'understand', dumps(guide), '[]', now(), 1,
            ))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.on('pageerror', lambda error: errors.append(str(error)))
            for chapter, question, choice in [('demo-network-layer', 0, 1), ('demo-network-layer', 1, 0), ('review-other', 2, 0)]:
                response = page.request.post(f'{url}/api/chapters/{chapter}/attempts', data={'question_index': question, 'choice': choice})
                assert response.ok
            page.goto(url + '/#chapter/demo-network-layer')
            expect(page.locator('#review-count')).to_have_text('3')
            expect(page.locator('.chapter-nav')).to_have_count(2)
            page.locator('#chapter-search').fill('实验 alpha')
            expect(page.locator('.chapter-nav')).to_have_count(1)
            expect(page.locator('[data-chapter="review-other"]')).to_be_visible()
            page.locator('#chapter-filter').select_option('unfinished')
            expect(page.locator('.chapter-nav')).to_have_count(0)
            expect(page.locator('#history-result')).to_have_text('显示 0 / 2 章')
            page.locator('#clear-history-filters').click()
            expect(page.locator('.chapter-nav')).to_have_count(2)
            page.locator('#chapter-filter').select_option('completed')
            expect(page.locator('.chapter-nav')).to_have_count(1)
            page.locator('#chapter-filter').select_option('all')
            page.locator('#chapter-search').fill('网络层')
            expect(page.locator('[data-chapter="demo-network-layer"]')).to_be_visible()
            page.locator('#chapter-search').fill('')
            checked.append('chapter search combines course, book, title and completion filters')

            page.get_by_role('tab', name='自测练习').click()
            page.locator('input[name="quiz-2"][value="1"]').check()
            page.locator('#open-review').click()
            expect(page.locator('.review-card')).to_have_count(3)
            expect(page.locator('#review-list .quiz-feedback')).to_have_count(0)
            expect(page.locator('#review-list input:checked')).to_have_count(0)
            assert not page.request.get(url + '/api/settings').json()['configured']
            rows = page.request.get(url + '/api/review').json()
            first = next(index for index, row in enumerate(rows) if row['chapter_id'] == 'demo-network-layer' and row['question_index'] == 0)
            second = next(index for index, row in enumerate(rows) if row['chapter_id'] == 'demo-network-layer' and row['question_index'] == 1)
            page.locator(f'input[name="review-{second}"][value="1"]').check()
            page.locator(f'input[name="review-{first}"][value="0"]').check()
            page.locator(f'[data-review-check="{first}"]').click()
            expect(page.locator(f'#review-{first} .quiz-feedback')).to_contain_text('已答对')
            expect(page.locator(f'input[name="review-{second}"][value="1"]')).to_be_checked()
            expect(page.locator('#review-count')).to_have_text('2')
            page.locator(f'[data-review-check="{second}"]').click()
            expect(page.locator(f'#review-{second} .quiz-feedback')).to_contain_text('正确答案是 C')
            expect(page.locator('#review-count')).to_have_text('2')
            page.locator(f'input[name="review-{second}"][value="2"]').check()
            page.locator(f'[data-review-check="{second}"]').click()
            expect(page.locator(f'#review-{second} .quiz-feedback')).to_contain_text('已答对')
            expect(page.locator('#review-count')).to_have_text('1')
            page.locator('[data-close="review-dialog"]').click()
            expect(page.locator('#quiz-0 .quiz-feedback')).to_contain_text('答对了')
            expect(page.locator('#quiz-1 .quiz-feedback')).to_contain_text('答对了')
            expect(page.locator('input[name="quiz-2"][value="1"]')).to_be_checked()
            checked.append('offline review reveals answers only after grading and synchronizes original quiz without losing other drafts')

            page.locator('#open-review').click()
            expect(page.locator('.review-card')).to_have_count(1)
            expect(page.locator('#review-list img')).to_have_count(0)
            assert page.evaluate('window.__reviewInjected === undefined')
            page.screenshot(path=str(output / 'review-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('#review-dialog').evaluate('(element) => element.scrollWidth <= element.clientWidth')
            page.screenshot(path=str(output / 'review-mobile.png'))
            checked.append('review escapes model and chapter text and fits a 390px mobile screen')
            page.locator('[data-review-open="0"]').click()
            expect(page.locator('#review-dialog')).not_to_be_visible()
            expect(page.locator('#export-link')).to_have_attribute('href', '/api/chapters/review-other/export')
            expect(page.locator('#tab-quiz')).to_have_attribute('aria-selected', 'true')
            expect(page.locator('#quiz-2')).to_be_focused()
            page.locator('#toggle-navigation').click()
            page.locator('#open-review').click()
            expect(page.locator('.review-card')).to_have_count(1)
            page.locator('input[name="review-0"][value="3"]').check()
            page.locator('[data-review-check="0"]').click()
            expect(page.locator('#review-summary')).to_contain_text('待复习 0 题')
            page.locator('#refresh-review').click()
            expect(page.locator('#review-summary')).to_have_text('目前没有待复习的错题')
            expect(page.locator('.review-card')).to_have_count(0)
            page.locator('[data-close="review-dialog"]').click()
            page.reload()
            expect(page.locator('#review-count')).to_have_text('0')
            checked.append('review jumps to the original question and solved items stay out of the queue after refresh')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False}
        (output / 'review-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
