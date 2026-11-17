"""Daily practice selects real questions without losing edits or duplicate navigation."""
import json
from playwright.sync_api import expect, sync_playwright
from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY


def run():
    server = LocalServer()
    errors, checked = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.locator('#open-rewards').click()
            expect(page.locator('#practice-recommendation')).to_contain_text('体系结构')
            expect(page.locator('#start-daily-practice')).to_be_enabled()
            page.route('**/api/course/network/lessons/*/open', lambda route: route.fulfill(status=503, json={'detail': '课时暂时无法打开'}))
            page.locator('#start-daily-practice').click()
            expect(page.locator('#rewards-error')).to_have_text('课时暂时无法打开')
            expect(page.locator('#start-daily-practice')).to_be_enabled()
            expect(page.locator('#rewards-dialog')).to_be_visible()
            page.unroute('**/api/course/network/lessons/*/open')
            checked.append('failed practice navigation keeps the growth panel open and allows retry')

            held = []
            page.route('**/api/course/network/lessons/*/open', lambda route: held.append((route, route.fetch())))
            page.locator('#start-daily-practice').click()
            expect(page.locator('#start-daily-practice')).to_be_disabled()
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(10)
            assert len(held) == 1
            page.evaluate("window.dispatchEvent(new Event('learning-data-imported'))")
            expect(page.locator('#rewards-content')).to_have_attribute('aria-busy', 'false')
            expect(page.locator('#start-daily-practice')).to_be_disabled()
            held[0][0].fulfill(response=held[0][1])
            expect(page.locator('#rewards-dialog')).not_to_be_visible()
            expect(page.locator('#chapter-title')).to_contain_text('体系结构')
            expect(page.locator('#quiz-0')).to_be_focused()
            page.unroute('**/api/course/network/lessons/*/open')
            checked.append('practice opens and focuses the actual question; growth refresh does not unlock an in-flight opening')

            quiz = page.request.get(server.url + '/api/chapters/net-v1-layers').json()['guide']['quiz']
            for index, question in enumerate(quiz):
                page.locator(f'input[name="quiz-{index}"][value="{question["answer"]}"]').check()
                page.locator(f'[data-check="{index}"]').click()
                expect(page.locator(f'#quiz-{index} .quiz-feedback')).to_be_visible()
            expect(page.locator('#reward-pill-label')).to_contain_text('50 积分')
            response = page.request.put(server.url + '/api/chapters/net-v1-layers/notes', data={'notes': '另一个窗口的学习笔记', 'expected_revision': 0})
            assert response.ok
            page.locator('#notes').fill('本窗口正在补充的理解')
            page.locator('#save-notes').click()
            expect(page.locator('#merge-notes')).to_be_visible()
            page.locator('#open-rewards').click()
            expect(page.locator('#practice-recommendation')).to_contain_text('速率、时延')
            expect(page.locator('#start-daily-practice')).to_have_text('继续巩固（可选）')
            page.locator('#start-daily-practice').click()
            expect(page.locator('#rewards-error')).to_contain_text('保存问题')
            expect(page.locator('#rewards-dialog')).to_be_visible()
            expect(page.locator('#notes')).to_have_value('本窗口正在补充的理解')
            page.locator('[data-close="rewards-dialog"]').click()
            page.locator('#merge-notes').click()
            expect(page.locator('#notes-status')).to_have_text('已保存到本机')
            page.locator('#open-rewards').click()
            page.locator('#start-daily-practice').click()
            expect(page.locator('#chapter-title')).to_contain_text('速率、时延')
            expect(page.locator('#quiz-0')).to_be_focused()
            notes = page.request.get(server.url + '/api/chapters/net-v1-layers').json()['notes']
            assert '另一个窗口' in notes and '本窗口' in notes
            checked.append('daily practice respects note conflicts and continues after both versions are merged')

            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#open-rewards').click()
            expect(page.locator('#start-daily-practice')).to_be_enabled()
            page.screenshot(path=str(OUTPUT / 'daily-practice-mobile.png'))
            page.locator('#start-daily-practice').click()
            expect(page.locator('#quiz-0')).to_be_focused()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            checked.append('optional continued practice is accessible on mobile without requiring model configuration')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False}
        (OUTPUT / 'daily-practice-browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
