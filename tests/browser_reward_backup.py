"""Restore only missing growth data, including retries after a lost response."""
import json
from playwright.sync_api import expect, sync_playwright
from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY
from backend.storage import db


def run():
    server = LocalServer()
    errors, checked = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            chapter_id = page.request.post(server.url + '/api/course/network/lessons/net-v1-layers/open').json()['chapter_id']
            quiz = page.request.get(server.url + '/api/chapters/' + chapter_id).json()['guide']['quiz']
            for index, question in enumerate(quiz):
                page.request.post(server.url + '/api/chapters/' + chapter_id + '/attempts', data={'question_index': index, 'choice': question['answer']})
            page.goto(server.url + '/#chapter/' + chapter_id)
            expect(page.locator('#reward-pill-label')).to_contain_text('50 积分')
            page.locator('#open-data').click()
            with page.expect_download() as download:
                page.locator('#backup-export').click()
            path = OUTPUT / 'reward-backup-download.json'
            download.value.save_as(str(path))
            document = json.loads(path.read_text(encoding='utf-8'))
            assert document['format_version'] == 2
            assert len(document['rewards']) == 4 and len(document['practice_days']) == 3
            checked.append('browser backup download contains all earned points and daily practice records')
            page.locator('[data-close="data-dialog"]').click()
            with db() as conn:
                conn.execute('DELETE FROM learning_rewards')
                conn.execute('DELETE FROM learning_days')
            page.reload()
            expect(page.locator('#reward-pill-label')).to_contain_text('0 积分')
            page.locator('#open-data').click()
            page.locator('#backup-file').set_input_files(str(path))
            expect(page.locator('#backup-preview-summary')).to_contain_text('新增 0 章，冲突副本 0 章')
            expect(page.locator('#backup-rewards-summary')).to_contain_text('4 项奖励（50 分）')
            expect(page.locator('#backup-import')).to_be_enabled()
            checked.append('an unchanged chapter backup can still restore missing growth data')

            def lose_response(route):
                response = page.request.post(route.request.url, headers={'Origin': server.url}, multipart={
                    'file': {'name': path.name, 'mimeType': 'application/json', 'buffer': path.read_bytes()}})
                assert response.ok, response.text()
                route.abort('failed')
            page.route('**/api/backups/import', lose_response)
            page.locator('#backup-import').click()
            expect(page.locator('#backup-error')).to_contain_text('无法连接本地服务')
            assert page.request.get(server.url + '/api/rewards').json()['points'] == 50
            page.unroute('**/api/backups/import')
            page.locator('#backup-import').click()
            expect(page.locator('#backup-success')).to_be_visible()
            expect(page.locator('#reward-pill-label')).to_contain_text('50 积分')
            assert page.request.get(server.url + '/api/rewards').json()['points'] == 50
            page.locator('#backup-file').set_input_files(str(path))
            expect(page.locator('#backup-rewards-summary')).to_contain_text('新增 0 项奖励（0 分）')
            expect(page.locator('#backup-import')).to_be_disabled()
            checked.append('retrying a committed import with a lost response cannot duplicate points and updates the growth display')

            document['format_version'] = 1
            document.pop('rewards')
            document.pop('practice_days')
            page.locator('#backup-file').set_input_files({'name': 'legacy.json', 'mimeType': 'application/json', 'buffer': json.dumps(document).encode('utf-8')})
            expect(page.locator('#backup-rewards-summary')).to_contain_text('旧版备份')
            page.set_viewport_size({'width': 320, 'height': 780})
            page.evaluate("document.documentElement.style.fontSize='32px'")
            assert page.locator('#data-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
            page.evaluate("document.documentElement.style.fontSize=''")
            page.screenshot(path=str(OUTPUT / 'reward-backup-mobile.png'))
            page.locator('[data-close="data-dialog"]').click()
            page.locator('#open-rewards').click()
            expect(page.locator('#growth-points')).to_have_text('50 学习积分')
            expect(page.locator('.growth-badge.earned')).to_have_count(2)
            expect(page.locator('#growth-daily-count')).to_have_text('3 / 3')
            checked.append('legacy backups are identified and restored badges and daily practice remain visible on mobile')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False}
        (OUTPUT / 'reward-backup-browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
