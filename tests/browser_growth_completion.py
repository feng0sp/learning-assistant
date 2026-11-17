"""Final growth level stays readable and still routes later mistakes to review."""
import json
from playwright.sync_api import expect, sync_playwright
from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY


def run():
    server = LocalServer()
    errors, checked = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 960})
            page.on('pageerror', lambda error: errors.append(str(error)))
            catalog = page.request.get(server.url + '/api/course/network').json()
            for lesson in catalog['lessons']:
                chapter_id = lesson['id']
                response = page.request.post(server.url + '/api/course/network/lessons/' + chapter_id + '/open')
                assert response.ok
                guide = page.request.get(server.url + '/api/chapters/' + chapter_id + '/study').json()['guide']
                for index, question in enumerate(guide['quiz']):
                    response = page.request.post(server.url + '/api/chapters/' + chapter_id + '/attempts', data={'question_index': index, 'choice': question['answer']})
                    assert response.ok
            page.goto(server.url)
            page.locator('#open-rewards').click()
            expect(page.locator('#growth-level')).to_have_text('Lv.5 · 学有所成')
            expect(page.locator('#growth-points')).to_have_text('1200 学习积分')
            expect(page.locator('.growth-badge.earned')).to_have_count(5)
            expect(page.locator('#growth-next')).to_contain_text('所有成长等级已解锁')
            expect(page.locator('#growth-daily')).to_contain_text('72 道不同课程题')
            expect(page.locator('#start-daily-practice')).to_be_enabled()
            expect(page.locator('#practice-recommendation')).to_contain_text('微课堂')
            assert page.locator('#growth-progress').evaluate('e => e.value === e.max')
            OUTPUT.mkdir(exist_ok=True)
            page.screenshot(path=str(OUTPUT / 'growth-complete-desktop.png'))
            checked.append('all 24 lessons unlock five badges and the final level without an invalid next-level target')
            page.set_viewport_size({'width': 320, 'height': 780})
            page.evaluate("document.documentElement.style.fontSize='32px'")
            assert page.locator('#rewards-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.locator('.growth-badge.earned').last.scroll_into_view_if_needed()
            expect(page.locator('.growth-badge.earned').last).to_be_visible()
            checked.append('the final badge remains reachable on a 320-pixel screen with doubled text size')
            page.locator('[data-close="rewards-dialog"]').click()
            first = catalog['lessons'][0]['id']
            answer = page.request.get(server.url + '/api/chapters/' + first).json()['guide']['quiz'][0]['answer']
            response = page.request.post(server.url + '/api/chapters/' + first + '/attempts', data={'question_index': 0, 'choice': (answer + 1) % 4})
            assert response.json()['reward_points'] == 0
            page.locator('#open-rewards').click()
            expect(page.locator('#growth-points')).to_have_text('1200 学习积分')
            expect(page.locator('#practice-recommendation')).to_contain_text('先巩固这道错题')
            expect(page.locator('#start-daily-practice')).to_be_enabled()
            page.locator('#start-daily-practice').click()
            expect(page.locator('#quiz-0')).to_be_focused()
            expect(page.locator('#rewards-dialog')).not_to_be_visible()
            checked.append('a later wrong answer keeps earned rewards and reopens the correct review question')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False}
        (OUTPUT / 'growth-completion-browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
