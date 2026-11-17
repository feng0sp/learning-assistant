"""Chapter completion writes remain isolated while navigating between chapters."""
import json

from playwright.sync_api import expect, sync_playwright

from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY
from backend.storage import db


def run():
    server = LocalServer()
    errors, checked = [], []
    try:
        with db() as conn:
            conn.execute("INSERT INTO chapters (id,course,textbook,title,mode,goal,guide,sources,notes,created_at) SELECT 'progress-second',course,textbook,'第二章进度测试',mode,goal,guide,sources,'',created_at FROM chapters WHERE id='demo-network-layer'")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.url + '/#chapter/demo-network-layer')
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            pending = {}
            def hold_progress(route):
                chapter_id = route.request.url.split('/')[-2]
                pending[chapter_id] = (route, route.fetch())
            page.route('**/api/chapters/*/progress', hold_progress)
            button = page.locator('#mark-complete')
            button.click()
            expect(button).to_be_disabled()
            page.locator('[data-chapter="progress-second"]').click()
            expect(page.locator('#chapter-title')).to_have_text('第二章进度测试')
            expect(button).to_be_enabled()
            button.click()
            expect(button).to_be_disabled()
            pending['demo-network-layer'][0].fulfill(response=pending['demo-network-layer'][1])
            page.wait_for_function('() => state.chapters.find(c => c.id === "demo-network-layer").completed')
            expect(button).to_be_disabled()
            checked.append('completion of a previous chapter does not unlock the current pending write')
            page.locator('[data-chapter="demo-network-layer"]').click()
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            expect(button).to_be_enabled()
            expect(button).to_contain_text('已学完')
            page.locator('[data-chapter="progress-second"]').click()
            expect(page.locator('#chapter-title')).to_have_text('第二章进度测试')
            expect(button).to_be_disabled()
            pending['progress-second'][0].fulfill(response=pending['progress-second'][1])
            expect(button).to_be_enabled()
            expect(button).to_contain_text('已学完')
            checked.append('returning to a chapter preserves its pending state until its own response arrives')
            page.unroute('**/api/chapters/*/progress')
            page.route('**/api/chapters/*/progress', lambda route: route.fulfill(status=503, json={'detail': '进度暂时无法保存'}))
            button.click()
            expect(page.locator('#toast')).to_have_text('进度暂时无法保存')
            expect(button).to_be_enabled()
            expect(button).to_contain_text('已学完')
            page.unroute('**/api/chapters/*/progress')
            button.click()
            expect(button).to_have_text('标记已学完')
            expect(button).to_be_enabled()
            page.reload()
            expect(page.locator('#chapter-title')).to_have_text('第二章进度测试')
            expect(button).to_have_text('标记已学完')
            checked.append('failed completion changes preserve the saved state and can be retried')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors, 'external_model_used': False}
        (OUTPUT / 'progress-race-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
