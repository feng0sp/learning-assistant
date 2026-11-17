"""Older answer paging remains retryable and belongs to its original chapter."""
import json
from playwright.sync_api import expect, sync_playwright
from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY
from backend.storage import db, now
from tests.fake_provider import FakeProvider


def run():
    server = LocalServer()
    provider = FakeProvider().start()
    errors, checked = [], []
    try:
        with db() as conn:
            conn.executemany('INSERT INTO messages(chapter_id,question,answer,created_at) VALUES (?,?,?,?)',
                [('demo-network-layer', f'问题 {i:03d}', f'回答 {i:03d}', now()) for i in range(95)])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(server.url)
            expect(page.locator('#messages .answer')).to_have_count(30)
            expect(page.locator('#messages .question').first).to_have_text('我想知道问题 065')
            endpoint = '**/api/chapters/demo-network-layer/messages?*'
            page.route(endpoint, lambda route: route.fulfill(status=503, json={'detail': '临时连接失败'}))
            page.locator('#older-messages').click()
            expect(page.locator('#toast')).to_contain_text('请重试')
            expect(page.locator('#older-messages')).to_be_enabled()
            expect(page.locator('#messages .answer')).to_have_count(30)
            page.unroute(endpoint)
            page.locator('#older-messages').click()
            expect(page.locator('#messages .answer')).to_have_count(60)
            expect(page.locator('#messages .question').first).to_have_text('我想知道问题 035')
            checked.append('failed older page preserves current messages and retries the same cursor')

            held = []
            page.route(endpoint, lambda route: held.append((route, route.fetch())))
            page.locator('#older-messages').click()
            expect(page.locator('#older-messages')).to_be_disabled()
            page.wait_for_function('() => document.querySelector("#older-messages").disabled')
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(10)
            assert len(held) == 1
            response = page.request.post(server.url + '/api/course/network/lessons/net-v1-layers/open')
            assert response.ok, response.text()
            page.evaluate("async () => { await refreshHistory(); await openChapter('net-v1-layers'); }")
            expect(page.locator('#chapter-title')).to_contain_text('体系结构')
            held[0][0].fulfill(response=held[0][1])
            page.wait_for_timeout(100)
            expect(page.locator('#messages .answer')).to_have_count(0)
            expect(page.locator('#older-messages')).to_be_hidden()
            page.unroute(endpoint)
            page.locator('[data-chapter="demo-network-layer"]').click()
            expect(page.locator('#messages .answer')).to_have_count(30)
            checked.append('late older-page response cannot populate another chapter or unlock its controls')

            for count in (60, 90, 95):
                page.locator('#older-messages').click()
                expect(page.locator('#messages .answer')).to_have_count(count)
            expect(page.locator('#older-messages')).to_be_hidden()
            expect(page.locator('#messages')).to_be_focused()
            questions = page.locator('#messages .question').all_text_contents()
            assert questions == [f'我想知道问题 {i:03d}' for i in range(95)]
            checked.append('all pages retain strict order without duplicates and final page restores keyboard focus')

            page.route('**/api/chapters/demo-network-layer/study', lambda route: route.fulfill(status=404, json={'detail': 'Not Found'}))
            page.reload()
            expect(page.locator('#messages .answer')).to_have_count(30)
            page.locator('#older-messages').click()
            expect(page.locator('#messages .answer')).to_have_count(60)
            checked.append('updated frontend remains compatible while an older local backend awaits restart')
            page.unroute('**/api/chapters/demo-network-layer/study')

            page.request.put(server.url + '/api/settings', data={'base_url': provider.base_url, 'model': 'test-double', 'api_key': 'test-only'})
            page.reload()
            expect(page.locator('#ask-button')).to_be_enabled()
            delayed = []
            page.route('**/api/chapters/demo-network-layer/questions', lambda route: delayed.append((route, route.fetch())))
            page.locator('#question').fill('较早提交但尚未收到的回答')
            page.locator('#ask-button').click()
            for _ in range(100):
                if delayed:
                    break
                page.wait_for_timeout(10)
            assert len(delayed) == 1
            with db() as conn:
                conn.executemany('INSERT INTO messages(chapter_id,question,answer,created_at) VALUES (?,?,?,?)',
                    [('demo-network-layer', f'另一窗口问题 {i:03d}', f'另一窗口回答 {i:03d}', now()) for i in range(40)])
            page.evaluate("async () => { await openChapter('net-v1-layers'); await openChapter('demo-network-layer'); }")
            expect(page.locator('#messages .answer')).to_have_count(30)
            delayed[0][0].fulfill(response=delayed[0][1])
            expect(page.locator('#ask-button')).to_be_enabled()
            page.locator('#older-messages').click()
            expect(page.locator('#messages .answer')).to_have_count(60)
            for count in (90, 120, 136):
                page.locator('#older-messages').click()
                expect(page.locator('#messages .answer')).to_have_count(count)
            expected = page.request.get(server.url + '/api/chapters/demo-network-layer').json()['messages']
            assert page.locator('#messages .question').all_text_contents() == ['我想知道' + item['question'] for item in expected]
            checked.append('a late answer older than the loaded range does not move the cursor, duplicate records or skip intervening answers')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'javascript_errors': errors}
        OUTPUT.mkdir(exist_ok=True)
        (OUTPUT / 'message-pages-browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
