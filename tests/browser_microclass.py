"""Offline companion course and cross-catalog navigation, using temporary data."""
import json
from playwright.sync_api import expect, sync_playwright
from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY


def run():
    server = LocalServer()
    errors, external, checked = [], [], []
    OUTPUT.mkdir(exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: external.append(request.url) if not request.url.startswith(server.url) else None)
            page.goto(server.url)
            page.locator('#open-microclass').click()
            expect(page.locator('.course-lesson')).to_have_count(12)
            expect(page.locator('.course-unit')).to_have_count(3)
            expect(page.locator('#course-intro')).to_contain_text('120 道选择题')
            expect(page.locator('[data-network-exam="1"]')).to_be_hidden()
            page.locator('.course-about summary').click()
            expect(page.locator('#microclass-source')).to_contain_text('尚未逐节核对视频字幕')
            expect(page.locator('#microclass-source a')).to_have_attribute('href', 'https://www.bilibili.com/video/BV1c4411d7jb/')
            page.locator('.course-about summary').click()
            page.screenshot(path=str(OUTPUT / 'microclass-desktop.png'))
            checked.append('prominent companion entry opens three chapters and 120 questions with explicit source limits')

            catalog = page.request.get(server.url + '/api/course/microclass').json()
            for item in catalog['lessons']:
                page.locator(f'.course-lesson [data-network-lesson="{item["id"]}"]').click()
                expect(page.locator('#chapter-title')).to_contain_text(item['title'])
                expect(page.locator('.knowledge-card')).to_have_count(6)
                expect(page.locator('#demo-notice')).to_contain_text('尚未逐节核对视频字幕')
                page.locator('#tab-examples').click()
                expect(page.locator('.example-card')).to_have_count(4)
                page.locator('#tab-analogies').click()
                expect(page.locator('.analogy-card')).to_have_count(1)
                page.locator('#tab-quiz').click()
                expect(page.locator('.quiz-card')).to_have_count(10)
                expect(page.locator('.quiz-feedback')).to_have_count(0)
                page.locator('#open-microclass').click()
                expect(page.locator('.course-lesson')).to_have_count(12)
            checked.append('all twelve lessons display six knowledge points, four examples, analogy and ten unanswered questions offline')

            page.locator('.course-lesson [data-network-lesson="micro-v1-capacity"]').click()
            expect(page.locator('#chapter-title')).to_contain_text('信道容量')
            page.locator('#notes').fill('先把 dB 换成线性信噪比。')
            page.locator('#save-notes').click()
            expect(page.locator('#notes-status')).to_have_text('已保存到本机')
            chapter = page.request.get(server.url + '/api/chapters/micro-v1-capacity').json()
            q = chapter['guide']['quiz'][8]
            page.locator('#tab-quiz').click()
            page.locator(f'input[name="quiz-8"][value="{(q["answer"]+1)%4}"]').check()
            page.locator('[data-check="8"]').click()
            expect(page.locator('#quiz-8 .quiz-feedback')).to_be_visible()
            for letter in 'ABCD':
                expect(page.locator('#quiz-8 .quiz-feedback')).to_contain_text(letter + '（')
            assert page.locator('#quiz-8 .quiz-feedback p').evaluate("e => getComputedStyle(e).whiteSpace") == 'pre-line'
            page.reload()
            expect(page.locator('#notes')).to_have_value('先把 dB 换成线性信噪比。')
            page.locator('#open-rewards').click()
            expect(page.locator('#practice-recommendation')).to_contain_text('微课堂 · 先巩固这道错题')
            page.locator('#start-daily-practice').click()
            expect(page.locator('#quiz-8')).to_be_focused()
            page.locator(f'input[name="quiz-8"][value="{q["answer"]}"]').check()
            page.locator('[data-check="8"]').click()
            expect(page.locator('#reward-pill-label')).to_contain_text('10 积分')
            page.locator('[data-check="8"]').click()
            expect(page.locator('#reward-pill-label')).to_contain_text('10 积分')
            page.screenshot(path=str(OUTPUT / 'microclass-explanation.png'))
            checked.append('later question feedback explains every option; note and answer survive reload; daily practice returns to microclass without duplicate points')

            page.locator('#open-course').click()
            expect(page.locator('.course-lesson')).to_have_count(24)
            page.locator('[data-close="course-dialog"]').click()
            expect(page.locator('#course-position')).to_contain_text('6 / 12')
            page.locator('#course-next').click()
            expect(page.locator('#chapter-title')).to_contain_text('封装成帧')
            checked.append('viewing the original catalog does not change the active companion lesson navigation')

            held = []
            page.route('**/api/course/network', lambda route: held.append(route))
            page.locator('#open-course').click()
            expect(page.locator('#course-lessons')).to_have_attribute('aria-busy', 'true')
            page.locator('[data-course-catalog="microclass"]').click()
            expect(page.locator('.course-lesson')).to_have_count(12)
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(10)
            assert held
            held.pop().fulfill(status=503, json={'detail': '旧目录的迟到错误'})
            page.wait_for_timeout(100)
            expect(page.locator('#course-error')).to_be_hidden()
            expect(page.locator('#course-title')).to_contain_text('微课堂')
            page.unroute('**/api/course/network')
            page.locator('[data-course-catalog="network"]').click()
            expect(page.locator('.course-lesson')).to_have_count(24)
            page.locator('[data-course-catalog="microclass"]').click()
            expect(page.locator('.course-lesson')).to_have_count(12)
            checked.append('delayed failures from the other catalog cannot overwrite the selected course and switching back can retry')

            page.locator('.course-lesson [data-network-lesson="micro-v1-internet"]').click()
            expect(page.locator('#chapter-title')).to_contain_text('网络与因特网')
            snapshot = page.request.get(server.url + '/api/chapters/micro-v1-internet').json()
            response = page.request.put(server.url + '/api/chapters/micro-v1-internet/notes', data={'notes': '另一窗口的笔记', 'expected_revision': snapshot['notes_revision']})
            assert response.ok
            page.locator('#notes').fill('本窗口的补充')
            page.locator('#save-notes').click()
            expect(page.locator('#merge-notes')).to_be_visible()
            page.locator('#course-next').click()
            expect(page.locator('#chapter-title')).to_contain_text('网络与因特网')
            expect(page.locator('#notes')).to_have_value('本窗口的补充')
            page.locator('#merge-notes').click()
            page.locator('#save-notes').click()
            expect(page.locator('#notes-status')).to_have_text('已保存到本机')
            page.locator('#course-next').click()
            expect(page.locator('#chapter-title')).to_contain_text('交换方式与性能指标')
            saved = page.request.get(server.url + '/api/chapters/micro-v1-internet').json()['notes']
            assert '本窗口的补充' in saved and '另一窗口的笔记' in saved
            checked.append('companion next-lesson navigation respects note conflicts and preserves both versions after merge')

            page.locator('#open-microclass').click()
            page.locator('#course-search').fill('CRC')
            expect(page.locator('.course-lesson')).to_have_count(1)
            page.locator('#course-search').fill('')
            expect(page.locator('.course-lesson')).to_have_count(12)
            for width in (320,390,720):
                page.set_viewport_size({'width': width, 'height': 844})
                page.evaluate("document.documentElement.style.fontSize='32px'")
                assert page.locator('#course-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate("document.documentElement.style.fontSize=''")
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(OUTPUT / 'microclass-mobile.png'))
            page.keyboard.press('Escape')
            expect(page.locator('#course-dialog')).not_to_be_visible()
            checked.append('knowledge search, keyboard dismissal and 320/390/720-pixel doubled-text layouts remain usable')

            backup = page.request.get(server.url + '/api/backups/export').json()
            assert len([c for c in backup['chapters'] if c['id'].startswith('micro-v1-')]) == 12
            response = page.request.post(server.url + '/api/backups/import', multipart={'file': {'name': 'micro.json', 'mimeType': 'application/json', 'buffer': json.dumps(backup).encode()}})
            assert response.ok and response.json()['points_added'] == 0 and response.json()['imported'] == 0
            checked.append('complete companion chapters, notes, quiz attempts and rewards round-trip in an idempotent backup')

            # Supplemental source links remain usable at large text sizes and
            # arbitrary imported text cannot introduce executable markup.
            page.locator('#tab-sources').click()
            expect(page.locator('.source-card')).to_have_count(2)
            expect(page.locator('.source-card').nth(1)).to_contain_text('复习自查')
            for link in page.locator('.source-card a').all():
                assert link.get_attribute('href').startswith('https://')
                expect(link).to_have_attribute('rel', 'noopener noreferrer')
                expect(link).to_have_attribute('target', '_blank')
            for width in (320,390,720):
                page.set_viewport_size({'width': width, 'height': 844})
                page.evaluate("document.documentElement.style.fontSize='32px'")
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate("document.documentElement.style.fontSize=''")
            page.set_viewport_size({'width': 1280, 'height': 1000})
            page.screenshot(path=str(OUTPUT / 'microclass-supplement-sources.png'))
            chapter = page.request.get(server.url + '/api/chapters/micro-v1-performance/study').json()
            chapter['sources'][0]['text'] = '<img src=x onerror=alert(1)> javascript:alert(1) https://example.org/check?q=1&ok=2。'
            page.route('**/api/chapters/micro-v1-performance/study', lambda route: route.fulfill(json=chapter))
            page.reload()
            expect(page.locator('#chapter-title')).to_contain_text('交换方式与性能指标')
            page.locator('#tab-sources').click()
            expect(page.locator('.source-card').first.locator('img,script')).to_have_count(0)
            expect(page.locator('.source-card').first.locator('pre')).to_contain_text('<img src=x onerror=alert(1)>')
            expect(page.locator('.source-card').first.locator('a')).to_have_attribute('href', 'https://example.org/check?q=1&ok=2')
            page.unroute('**/api/chapters/micro-v1-performance/study')
            checked.append('supplement sources and HTTP links are readable at doubled text size; imported HTML and non-HTTP schemes stay inert')
            assert not errors, errors
            assert not external, external
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_requests': external, 'external_model_used': False}
        (OUTPUT / 'microclass-browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
