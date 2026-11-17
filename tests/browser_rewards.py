"""Reward feedback, focus mode and responsive growth panel."""
import json
from playwright.sync_api import expect, sync_playwright
from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY


def run():
    server = LocalServer()
    errors, checked = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            chapter_id = page.request.post(server.url + '/api/course/network/lessons/net-v1-layers/open').json()['chapter_id']
            quiz = page.request.get(server.url + '/api/chapters/' + chapter_id).json()['guide']['quiz']
            page.goto(server.url + '/#chapter/' + chapter_id)
            expect(page.locator('#chapter-title')).to_contain_text('体系结构')
            expect(page.locator('#reward-pill-label')).to_contain_text('0 积分')
            page.get_by_role('tab', name='自测练习').click()
            for index, question in enumerate(quiz):
                page.locator(f'input[name="quiz-{index}"][value="{question["answer"]}"]').check()
                page.locator(f'[data-check="{index}"]').click()
                expect(page.locator(f'#quiz-{index} .quiz-feedback')).to_be_visible()
            expect(page.locator('#reward-notice')).to_contain_text('+30')
            expect(page.locator('#reward-pill-label')).to_contain_text('50 积分')
            page.locator('#open-rewards').click()
            expect(page.locator('#growth-points')).to_have_text('50 学习积分')
            expect(page.locator('.growth-badge.earned')).to_have_count(2)
            expect(page.locator('#growth-daily-count')).to_have_text('3 / 3')
            page.screenshot(path=str(OUTPUT / 'learning-rewards-desktop.png'))
            page.locator('[data-close="rewards-dialog"]').click()
            page.reload()
            expect(page.locator('#reward-pill-label')).to_contain_text('50 积分')
            checked.append('course answers earn one-time points, a lesson bonus and visible badges that survive reload')

            page.locator('#toggle-focus').click()
            expect(page.locator('#toggle-focus')).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('.sidebar')).not_to_be_visible()
            expect(page.locator('.course-launch')).not_to_be_visible()
            expect(page.locator('#notes')).to_be_visible()
            page.reload()
            expect(page.locator('#chapter-title')).to_contain_text('体系结构')
            expect(page.locator('#toggle-focus')).to_have_attribute('aria-pressed', 'true')
            page.screenshot(path=str(OUTPUT / 'focused-reading.png'))
            page.locator('#toggle-focus').click()
            expect(page.locator('.sidebar')).to_be_visible()
            checked.append('focus mode preserves reading and notes, survives reload and restores navigation')

            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#open-rewards').click()
            expect(page.locator('#growth-points')).to_have_text('50 学习积分')
            page.screenshot(path=str(OUTPUT / 'learning-rewards-mobile.png'))
            for width in (320, 390, 720):
                page.set_viewport_size({'width': width, 'height': 844})
                page.evaluate("document.documentElement.style.fontSize='32px'")
                assert page.locator('#rewards-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1'), width
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.evaluate("document.documentElement.style.fontSize=''")
            page.locator('[data-close="rewards-dialog"]').click()
            checked.append('growth panel works at narrow widths and double text size')

            page.route('**/api/rewards', lambda route: route.fulfill(status=503, json={'detail': '成长记录暂时不可用'}))
            page.locator('#open-rewards').click()
            expect(page.locator('#rewards-error')).to_have_text('成长记录暂时不可用')
            page.unroute('**/api/rewards')
            page.locator('#rewards-retry').click()
            expect(page.locator('#rewards-error')).not_to_be_visible()
            expect(page.locator('#growth-points')).to_have_text('50 学习积分')
            checked.append('failed growth reads retain existing display and support retry')
            page.locator('[data-close="rewards-dialog"]').click()
            held = []
            page.route('**/api/course/network', lambda route: held.append((route, route.fetch())))
            page.evaluate("() => { document.querySelector('#open-course').click(); document.querySelector('#open-course').click(); document.querySelector('#open-course').click(); }")
            expect(page.locator('#course-lessons')).to_have_attribute('aria-busy', 'true')
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(10)
            assert len(held) == 1, len(held)
            held[0][0].fulfill(response=held[0][1])
            expect(page.locator('.course-lesson')).to_have_count(24)
            page.unroute('**/api/course/network')
            renders = page.evaluate('''async () => {
                let renders = 0;
                const observer = new MutationObserver(records => { renders += records.length; });
                observer.observe(document.querySelector('#course-lessons'), {childList: true});
                const input = document.querySelector('#course-search');
                for (let i=0; i<50; i++) {
                    input.value = i === 49 ? 'TCP' : String(i);
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                }
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                observer.disconnect();
                return renders;
            }''')
            assert renders == 1, renders
            expect(page.locator('.course-lesson')).not_to_have_count(24)
            checked.append('three overlapping catalog opens use one request; fifty same-frame search edits use one render')
            page.locator('[data-close="course-dialog"]').click()
            page.request.post(server.url + '/api/course/network/lessons/net-v1-delay/open')
            question = page.request.get(server.url + '/api/chapters/net-v1-delay').json()['guide']['quiz'][0]
            page.request.post(server.url + '/api/chapters/net-v1-delay/attempts', data={'question_index': 0, 'choice': question['answer']})
            page.locator('#course-next').click()
            expect(page.locator('#chapter-title')).to_contain_text('速率、时延')
            expect(page.locator('#reward-pill-label')).to_contain_text('60 积分')
            checked.append('chapter navigation refreshes rewards earned by another window without background polling')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors}
        (OUTPUT / 'rewards-browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
