"""End-to-end offline course, progress, navigation and calculator checks."""
import json

from playwright.sync_api import expect, sync_playwright
from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY


def run():
    server = LocalServer()
    errors, requests, checked = [], [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            page.goto(server.url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.locator('#open-course').click()
            expect(page.locator('.course-lesson')).to_have_count(24)
            expect(page.locator('.course-unit')).to_have_count(8)
            expect(page.locator('#course-summary')).to_contain_text('0 / 24')
            page.locator('#course-search').fill('CIDR')
            expect(page.locator('.course-lesson')).to_have_count(1)
            page.locator('#course-search').fill('没有这样的课')
            expect(page.locator('#course-lessons')).to_contain_text('没有匹配')
            page.locator('#course-search').fill('')
            page.screenshot(path=str(OUTPUT / 'network-course-desktop.png'))
            page.locator('#course-lessons [data-network-lesson="net-v1-layers"]').click()
            expect(page.locator('#chapter-title')).to_contain_text('体系结构')
            expect(page.locator('#mode-badge')).to_have_text('内置原创课程')
            expect(page.locator('#course-previous')).to_be_disabled()
            expect(page.locator('.knowledge-card')).to_have_count(3)
            checked.append('24 lessons across 8 units are searchable and open without model configuration')

            page.locator('#notes').fill('先看协议，再看服务。')
            page.locator('#course-next').click()
            expect(page.locator('#chapter-title')).to_contain_text('速率、时延')
            page.locator('#course-previous').click()
            expect(page.locator('#notes')).to_have_value('先看协议，再看服务。')
            page.reload()
            expect(page.locator('#course-position')).to_contain_text('1 / 24')
            expect(page.locator('#notes')).to_have_value('先看协议，再看服务。')
            checked.append('previous/next navigation saves notes and restores course navigation after reload')

            page.get_by_role('tab', name='自测练习').click()
            page.locator('input[name="quiz-0"][value="0"]').check()
            page.locator('[data-check="0"]').click()
            expect(page.locator('#quiz-0 .quiz-feedback')).to_be_visible()
            page.locator('#mark-complete').click()
            expect(page.locator('#mark-complete')).to_contain_text('已学完')
            expect(page.locator('#mark-complete')).to_be_enabled()
            page.locator('#open-course').click()
            expect(page.locator('#course-summary')).to_contain_text('1 / 24')
            expect(page.locator('#course-summary')).to_contain_text('已答 1 / 72')
            expect(page.locator('#course-continue')).to_contain_text('速率、时延')
            page.locator('#course-filter').select_option('wrong')
            expect(page.locator('.course-lesson')).to_have_count(1)
            page.locator('#course-filter').select_option('completed')
            expect(page.locator('.course-lesson')).to_have_count(1)
            page.locator('#course-filter').select_option('all')
            page.locator('[data-network-exam="1"]').click()
            expect(page.locator('#chapter-title')).to_contain_text('综合练习卷 1')
            expect(page.locator('.quiz-card')).to_have_count(12)
            expect(page.locator('#course-reading-nav')).not_to_be_visible()
            checked.append('completion and correct answers remain distinct; filters and comprehensive practice work')

            page.locator('#open-network-labs').click()
            page.locator('#network-lab-calculate').click()
            expect(page.locator('#network-lab-result-title')).to_have_text('192.168.1.64/26')
            expect(page.locator('#network-lab-steps')).to_contain_text('数量：62')
            page.locator('[name="address"]').fill('300.1.1.1/24')
            page.locator('#network-lab-calculate').click()
            expect(page.locator('#network-lab-error')).to_contain_text('有效的 IPv4')
            page.locator('#network-lab-type').select_option('delay')
            page.locator('#network-lab-calculate').click()
            expect(page.locator('#network-lab-result-title')).to_contain_text('6.2 ms')
            page.locator('#network-lab-type').select_option('crc')
            page.locator('#network-lab-calculate').click()
            expect(page.locator('#network-lab-result-title')).to_have_text('CRC 余数：001')
            expect(page.locator('#network-lab-steps')).to_contain_text('1101001')
            checked.append('subnet, delay and CRC calculators explain correct results and recover from invalid input')

            held = []
            page.route('**/api/course/network/labs/crc', lambda route: held.append((route, route.fetch())))
            page.locator('#network-lab-calculate').click()
            expect(page.locator('#network-lab-calculate')).to_be_disabled()
            page.locator('#network-lab-type').select_option('subnet')
            held[0][0].fulfill(response=held[0][1])
            expect(page.locator('#network-lab-calculate')).to_be_enabled()
            expect(page.locator('#network-lab-result')).not_to_be_visible()
            page.unroute('**/api/course/network/labs/crc')
            page.locator('[data-close="network-labs-dialog"]').click()
            checked.append('late calculator results cannot overwrite a newly selected experiment')

            for lesson in page.request.get(server.url + '/api/course/network').json()['lessons']:
                chapter_id = page.request.post(server.url + '/api/course/network/lessons/' + lesson['id'] + '/open').json()['chapter_id']
                page.goto(server.url + '/#chapter/' + chapter_id)
                expect(page.locator('#chapter-title')).to_contain_text(lesson['title'])
                expect(page.locator('.knowledge-card')).to_have_count(3)
                page.get_by_role('tab', name='例题解析').click()
                page.locator('.example-card summary').click()
                expect(page.locator('.answer-box')).to_be_visible()
                page.get_by_role('tab', name='生活类比').click()
                expect(page.locator('.analogy-card')).to_have_count(1)
                page.get_by_role('tab', name='自测练习').click()
                expect(page.locator('.quiz-card')).to_have_count(3)
            expect(page.locator('#course-next')).to_be_disabled()
            checked.append('every lesson renders knowledge, worked solution, analogy and three quiz questions')

            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#open-course').click()
            expect(page.locator('.course-lesson')).to_have_count(24)
            page.screenshot(path=str(OUTPUT / 'network-course-mobile.png'))
            for width in (320, 390, 720):
                page.set_viewport_size({'width': width, 'height': 844})
                page.evaluate("document.documentElement.style.fontSize='32px'")
                assert page.locator('#course-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate("document.documentElement.style.fontSize=''")
            page.locator('[data-close="course-dialog"]').click()
            checked.append('course catalog remains usable at 320/390/720 pixels with double text size')

            page.route('**/api/course/network', lambda route: route.fulfill(status=503, json={'detail': '课程暂时无法读取'}))
            page.locator('#open-course').click()
            expect(page.locator('#course-error')).to_have_text('课程暂时无法读取')
            page.unroute('**/api/course/network')
            page.locator('#course-retry').click()
            expect(page.locator('#course-error')).not_to_be_visible()
            expect(page.locator('.course-lesson')).to_have_count(24)
            checked.append('failed curriculum refresh retains content and supports retry')
            page.locator('[data-close="course-dialog"]').click()
            current = 'net-v1-troubleshooting'
            response = page.request.put(server.url + '/api/chapters/' + current + '/notes', data={'notes': '另一窗口的排障笔记', 'expected_revision': 0})
            assert response.ok
            page.locator('#notes').fill('当前窗口的排障理解')
            page.locator('#save-notes').click()
            expect(page.locator('#merge-notes')).to_be_visible()
            page.locator('#open-course').click()
            page.locator('#course-lessons [data-network-lesson="net-v1-layers"]').click()
            expect(page.locator('#course-error')).to_contain_text('保存问题')
            expect(page.locator('#course-dialog')).to_be_visible()
            expect(page.locator('#notes')).to_have_value('当前窗口的排障理解')
            page.locator('[data-close="course-dialog"]').click()
            page.locator('#merge-notes').click()
            expect(page.locator('#notes-status')).to_have_text('已保存到本机')
            page.locator('#course-previous').click()
            expect(page.locator('#chapter-title')).to_contain_text('密码学')
            saved = page.request.get(server.url + '/api/chapters/' + current).json()['notes']
            assert '另一窗口' in saved and '当前窗口' in saved
            checked.append('course navigation preserves conflicting notes and can continue after merging')
            assert not errors, errors
            assert all(url.startswith(server.url) for url in requests), requests
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False, 'external_requests': 0}
        (OUTPUT / 'network-course-browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
