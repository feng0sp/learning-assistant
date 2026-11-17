"""Quiz submissions remain locked when another answer causes a rerender."""
import copy
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-quiz-pending-')
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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.get_by_role('tab', name='自测练习').click()
            pending = []
            def hold_first(route):
                if route.request.post_data_json['question_index'] == 0:
                    pending.append((route, route.fetch()))
                else:
                    route.continue_()
            endpoint = url + '/api/chapters/demo-network-layer/attempts'
            page.route(endpoint, hold_first)
            page.locator('input[name="quiz-0"][value="1"]').check()
            page.locator('[data-check="0"]').click()
            expect(page.locator('[data-check="0"]')).to_be_disabled()
            page.locator('input[name="quiz-1"][value="0"]').check()
            page.locator('[data-check="1"]').click()
            expect(page.locator('#quiz-1 .quiz-feedback')).to_be_visible()
            expect(page.locator('[data-check="0"]')).to_be_disabled()
            page.locator('#open-review').click()
            review_card = page.locator('.review-card').filter(has_text=DEMO_GUIDE['quiz'][0]['question'])
            expect(review_card.locator('[data-review-check]')).to_be_disabled()
            page.locator('[data-close="review-dialog"]').click()
            page.get_by_role('tab', name='生活类比').click()
            page.get_by_role('tab', name='自测练习').click()
            expect(page.locator('[data-check="0"]')).to_be_disabled()
            page.locator('input[name="quiz-0"][value="0"]').check()
            assert len(pending) == 1
            pending[0][0].fulfill(response=pending[0][1])
            expect(page.locator('[data-check="0"]')).to_be_enabled()
            expect(page.locator('input[name="quiz-0"][value="0"]')).to_be_checked()
            expect(page.locator('input[name="quiz-0"][value="0"]')).to_be_focused()
            expect(page.locator('#quiz-0 .quiz-feedback')).not_to_be_visible()
            page.unroute(endpoint, hold_first)
            page.locator('[data-check="0"]').click()
            expect(page.locator('#quiz-0 .quiz-feedback')).to_contain_text('答对了')
            checked.append('other responses and tab switches cannot unlock a pending quiz submission; edits survive its completion')
            review_pending = []
            def hold_review(route):
                review_pending.append((route, route.fetch()))
            page.route(endpoint, hold_review)
            page.locator('#open-review').click()
            review_card = page.locator('.review-card').filter(has_text=DEMO_GUIDE['quiz'][1]['question'])
            review_card.locator(f'input[value="{DEMO_GUIDE["quiz"][1]["answer"]}"]').check()
            review_card.locator('[data-review-check]').click()
            page.locator('[data-close="review-dialog"]').click()
            expect(page.locator('[data-check="1"]')).to_be_disabled()
            page.get_by_role('tab', name='生活类比').click()
            page.get_by_role('tab', name='自测练习').click()
            expect(page.locator('[data-check="1"]')).to_be_disabled()
            assert len(review_pending) == 1
            review_pending[0][0].fulfill(response=review_pending[0][1])
            expect(page.locator('[data-check="1"]')).to_be_enabled()
            expect(page.locator('#quiz-1 .quiz-feedback')).to_contain_text('答对了')
            page.unroute(endpoint, hold_review)
            checked.append('quiz and review share the same pending lock even after closing the review dialog')
            page.request.post(endpoint, data={'question_index': 0, 'choice': 1})
            review_pending.clear()
            page.route(endpoint, hold_review)
            page.locator('#open-review').click()
            review_card = page.locator('.review-card').filter(has_text=DEMO_GUIDE['quiz'][0]['question'])
            review_card.locator('input[value="0"]').check()
            review_card.locator('[data-review-check]').click()
            page.locator('[data-close="review-dialog"]').click()
            page.locator('input[name="quiz-0"][value="2"]').check()
            assert len(review_pending) == 1
            review_pending[0][0].fulfill(response=review_pending[0][1])
            expect(page.locator('[data-check="0"]')).to_be_enabled()
            expect(page.locator('input[name="quiz-0"][value="2"]')).to_be_checked()
            expect(page.locator('#quiz-0 .quiz-feedback')).not_to_be_visible()
            page.unroute(endpoint, hold_review)
            checked.append('a delayed review result preserves a newer choice made in the original chapter')
            page.locator('input[name="quiz-0"][value="0"]').check()
            page.route(endpoint, lambda route: route.fulfill(status=503, json={'detail': '作答暂时无法保存'}))
            page.locator('[data-check="0"]').click()
            expect(page.locator('#toast')).to_have_text('作答暂时无法保存')
            expect(page.locator('[data-check="0"]')).to_be_enabled()
            expect(page.locator('input[name="quiz-0"][value="0"]')).to_be_checked()
            checked.append('a failed quiz write releases its lock and preserves the selected answer')
            page.unroute(endpoint)
            page.request.post(endpoint, data={'question_index': 0, 'choice': 1})
            page.request.post(endpoint, data={'question_index': 1, 'choice': 0})
            review_pending.clear()
            page.route(endpoint, hold_review)
            page.locator('#open-review').click()
            first_card = page.locator('.review-card').filter(has_text=DEMO_GUIDE['quiz'][0]['question'])
            second_card = page.locator('.review-card').filter(has_text=DEMO_GUIDE['quiz'][1]['question'])
            first_card.locator('input[value="0"]').check()
            first_card.locator('[data-review-check]').click()
            second_card.locator('input[value="2"]').check()
            assert len(review_pending) == 1
            review_pending[0][0].fulfill(response=review_pending[0][1])
            expect(first_card.locator('.quiz-feedback')).to_be_visible()
            expect(second_card.locator('input[value="2"]')).to_be_checked()
            expect(second_card.locator('input[value="2"]')).to_be_focused()
            checked.append('a completed review answer preserves focus on another question being edited')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors}
        (output / 'quiz-pending-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
