"""Focused checks for imported/model text, long labels, and stale browser state."""
import json
import time

import httpx
from playwright.sync_api import expect, sync_playwright

from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY
from tests.fake_provider import FakeProvider


def run():
    provider = FakeProvider().start()
    server = LocalServer()
    passed, errors = [], []
    try:
        with httpx.Client(base_url=server.url, trust_env=False, timeout=20) as client:
            client.put("/api/settings", json={"base_url": provider.base_url, "model": "edge-test-model", "api_key": "test-only"}).raise_for_status()
            provider.mode = "html_content"
            title = "VeryLongUnbrokenTitle" * 8 + "<b>标题</b>"
            response = client.post("/api/chapters/generate", json={"course": "CourseWithoutSpaces" * 5, "chapter": title, "mode": "material", "text": '教材正文：网络层负责跨网络传递分组，IP 提供尽力而为的交付。<script>window.__injected=1</script>'})
            response.raise_for_status()
            for _ in range(100):
                job = client.get('/api/jobs/' + response.json()["job_id"]).json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(.05)
            assert job["status"] == "completed", job
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(server.url + '/#chapter/' + job["chapter_id"])
                expect(page.locator("#chapter-title")).to_have_text(title)
                assert page.locator("#chapter-title b").count() == 0
                assert page.locator("#overview-text img").count() == 0
                assert page.locator(".knowledge-card script,.knowledge-card svg").count() == 0
                assert page.evaluate("window.__injected") is None
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "long labels overflow at mobile width"
                page.locator(".source-link").first.click()
                expect(page.locator(".source-card pre")).to_contain_text("<script>")
                assert page.locator(".source-card script").count() == 0
                page.get_by_role("tab", name="自测练习").click()
                assert page.locator(".quiz-option img").count() == 0
                passed.append("HTML in chapter, model output, quiz and imported sources is rendered as text")

                page.locator("#question").fill("请解释这个章节。")
                page.locator("#ask-button").click()
                expect(page.locator(".message.answer")).to_contain_text("<img", timeout=10000)
                assert page.locator(".message.answer img").count() == 0
                expect(page.locator(".message.answer .source-link")).to_have_text("资料 1")
                page.locator(".message.answer .source-link").click()
                expect(page.locator(".source-card")).to_have_class("source-card highlight")
                assert page.evaluate("window.__injected") is None
                passed.append("follow-up citations navigate to real sources without executing HTML")

                page.locator("#new-chapter").click()
                page.locator("#configure-from-chapter").click()
                expect(page.locator("#settings-dialog")).to_be_visible()
                page.locator('[data-close="settings-dialog"]').click()
                expect(page.locator("#chapter-dialog")).to_be_visible()
                page.locator('[data-close="chapter-dialog"]').click()
                passed.append("model settings can be opened and closed inside the chapter workflow")

                page.evaluate("localStorage.setItem('zhizhang-active-job','missing-job')")
                page.reload()
                expect(page.locator("#job-banner")).to_contain_text("上次生成任务已不存在")
                assert page.evaluate("localStorage.getItem('zhizhang-active-job')") is None
                passed.append("stale generation identifiers stop polling and offer a retry")

                page.evaluate("localStorage.setItem('zhizhang-draft','not valid json')")
                page.reload()
                expect(page.locator("#chapter-title")).to_have_text(title)
                page.locator("#new-chapter").click()
                expect(page.locator("#chapter-dialog")).to_be_visible()
                page.locator('[data-close="chapter-dialog"]').click()
                passed.append("malformed browser draft does not prevent using the app")

                page.evaluate("""localStorage.setItem('zhizhang-draft', JSON.stringify({
                    mode: 'topic', chapter: '第三章 数据链路层',
                    material: {id: 'damaged-material', outline: 'invalid-directory'}
                }))""")
                page.reload()
                page.locator('#new-chapter').click()
                expect(page.locator('#topic-help')).to_be_visible()
                expect(page.locator('#material-fields')).not_to_be_visible()
                expect(page.locator('#title-input')).to_have_value('第三章 数据链路层')
                page.locator('[name="mode"][value="material"]').check()
                expect(page.locator('#material-preview')).not_to_be_visible()
                expect(page.locator('#outline-picker')).not_to_be_visible()
                page.locator('[data-close="chapter-dialog"]').click()
                passed.append('damaged material metadata is discarded while the chapter title and source mode recover')
                assert not errors, errors
                browser.close()
        report = {"passed": passed, "javascript_errors": errors}
        (OUTPUT / 'browser-edges-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
