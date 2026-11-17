"""End-to-end browser checks against isolated local data and an explicit fake model."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix="zhizhang-browser-")
os.environ["STUDY_DATA_DIR"] = TEST_DIRECTORY.name
for key in ("STUDY_API_BASE_URL", "STUDY_API_KEY", "STUDY_MODEL"):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from tests.fake_provider import FakeProvider

OUTPUT = Path(__file__).resolve().parent.parent / "test-results"
OUTPUT.mkdir(exist_ok=True)


def run():
    provider = FakeProvider().start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    errors = []
    checked = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.locator("#chapter-title")).to_contain_text("网络层")
            expect(page.locator(".knowledge-card")).to_have_count(4)
            page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
            checked.append("desktop demo loading")

            page.get_by_role("tab", name="例题解析").click()
            page.locator(".example-card summary").first.click()
            expect(page.locator(".answer-box").first).to_be_visible()
            page.get_by_role("tab", name="生活类比").click()
            expect(page.locator(".limitation")).to_have_count(2)
            page.get_by_role("tab", name="自测练习").click()
            page.locator('input[name="quiz-0"][value="1"]').check()
            page.locator('input[name="quiz-1"][value="2"]').check()
            page.locator('[data-check="0"]').click()
            expect(page.locator("#quiz-0 .quiz-feedback")).to_contain_text("正确答案是 A")
            expect(page.locator('input[name="quiz-1"][value="2"]')).to_be_checked()
            page.get_by_role("tab", name="生活类比").click()
            page.get_by_role("tab", name="自测练习").click()
            expect(page.locator('input[name="quiz-1"][value="2"]')).to_be_checked()
            page.locator('input[name="quiz-0"][value="0"]').check()
            page.locator('[data-check="0"]').click()
            expect(page.locator("#quiz-0 .quiz-feedback")).to_contain_text("答对了")
            page.locator("#notes").fill("浏览器验证：转发执行下一跳，路由选择确定路径。")
            page.locator("#save-notes").click()
            expect(page.locator("#notes-status")).to_have_text("已保存到本机")
            page.locator("#mark-complete").click()
            expect(page.locator("#mark-complete")).to_contain_text("已学完")
            page.reload()
            expect(page.locator("#notes")).to_have_value("浏览器验证：转发执行下一跳，路由选择确定路径。")
            expect(page.locator("#mark-complete")).to_contain_text("已学完")
            page.get_by_role("tab", name="自测练习").click()
            expect(page.locator("#quiz-0 .quiz-feedback")).to_contain_text("答对了")
            checked.append("examples, analogies, quiz retry, notes and progress persistence")

            other = browser.new_page()
            other.goto(url + "/#chapter/demo-network-layer")
            expect(other.locator("#notes")).to_have_value("浏览器验证：转发执行下一跳，路由选择确定路径。")
            page.locator("#notes").fill("窗口一的新理解：转发是局部操作。")
            page.locator("#save-notes").click()
            expect(page.locator("#notes-status")).to_have_text("已保存到本机")
            other.locator("#notes").fill("窗口二的新理解：路由选择负责路径。")
            other.locator("#save-notes").click()
            expect(other.locator("#merge-notes")).to_be_visible()
            expect(other.locator("#notes")).to_have_value("窗口二的新理解：路由选择负责路径。")
            other.locator("#merge-notes").click()
            expect(other.locator("#notes-status")).to_have_text("已保存到本机")
            merged_notes = other.locator("#notes").input_value()
            assert "窗口一的新理解" in merged_notes and "窗口二的新理解" in merged_notes
            other.reload()
            expect(other.locator("#notes")).to_have_value(merged_notes)
            other.close()
            checked.append("two-window note conflicts preserve and merge both versions")

            page.locator("#new-chapter").click()
            page.locator("#title-input").fill("网络层测试章节")
            page.locator('[name="mode"][value="topic"]').check()
            page.locator("#generate-button").click()
            expect(page.locator("#generation-error")).to_contain_text("先配置")
            page.locator('[data-close="chapter-dialog"]').click()
            checked.append("missing model configuration message")

            page.locator("#open-settings").click()
            page.locator("#base-url").fill(provider.base_url)
            page.locator("#model-name").fill("browser-test-model")
            page.locator("#api-key").fill("browser-test-key")
            page.locator("#save-settings").click()
            expect(page.locator("#settings-success")).to_contain_text("设置已保存")
            page.locator("#test-model").click()
            expect(page.locator("#settings-success")).to_contain_text("连接成功")
            expect(page.locator("#api-key")).to_have_value("")
            page.locator('[data-close="settings-dialog"]').click()
            checked.append("model configuration, redaction and HTTP adapter")

            page.locator("#new-chapter").click()
            page.locator("#title-input").fill("按标题生成的网络层")
            page.locator('[name="mode"][value="topic"]').check()
            page.locator("#generate-button").click()
            expect(page.locator("#chapter-title")).to_have_text("按标题生成的网络层", timeout=15000)
            expect(page.locator("#topic-notice")).to_be_visible()
            page.get_by_role("tab", name="资料来源").click()
            expect(page.locator(".empty-state")).to_contain_text("还没有教材原文")
            checked.append("topic generation through real HTTP test double")

            page.locator("#new-chapter").click()
            page.locator("#title-input").fill("根据笔记生成的网络层")
            page.locator('[name="mode"][value="material"]').check()
            page.locator("#file-input").set_input_files({"name": "network.txt", "mimeType": "text/plain", "buffer": ("网络层负责跨网络传递分组。IP 提供尽力而为的交付。路由器采用最长前缀匹配进行转发。" * 4).encode("utf-8")})
            expect(page.locator("#material-preview")).to_be_visible()
            page.locator("#generate-button").click()
            expect(page.locator("#chapter-title")).to_have_text("根据笔记生成的网络层", timeout=15000)
            expect(page.locator("#mode-badge")).to_have_text("根据资料整理")
            page.locator(".source-link").first.click()
            expect(page.locator(".source-card")).to_contain_text("network.txt")
            page.locator("#question").fill("为什么 IP 不保证可靠交付？")
            page.locator("#ask-button").click()
            expect(page.locator(".message.answer")).to_contain_text("测试服务", timeout=10000)
            page.reload()
            expect(page.locator(".message.answer")).to_contain_text("测试服务")
            page.locator("#notes").fill("导出前刚输入、还没等待自动保存的笔记。")
            with page.expect_download() as download:
                page.locator("#export-link").click()
            download.value.save_as(str(OUTPUT / "export.md"))
            assert "根据笔记生成的网络层" in (OUTPUT / "export.md").read_text(encoding="utf-8")
            assert "还没等待自动保存" in (OUTPUT / "export.md").read_text(encoding="utf-8")
            checked.append("upload, grounded generation, citations, follow-up persistence and export")

            provider.mode = "unauthorized"
            page.locator("#new-chapter").click()
            page.locator("#title-input").fill("失败后可以重试的章节")
            page.locator('[name="mode"][value="topic"]').check()
            page.locator("#generate-button").click()
            expect(page.locator("#job-banner")).to_contain_text("本次生成未完成", timeout=15000)
            page.locator("#retry-generation").click()
            expect(page.locator("#title-input")).to_have_value("失败后可以重试的章节")
            page.locator('[data-close="chapter-dialog"]').click()
            checked.append("provider failure and draft-preserving retry")

            for width, height in ((390, 844), (768, 1024), (1280, 900)):
                page.set_viewport_size({"width": width, "height": height})
                page.evaluate("window.scrollTo(0,0)")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"horizontal overflow at {width}"
                page.screenshot(path=str(OUTPUT / f"viewport-{width}.png"), full_page=True)
            checked.append("390px, 768px and 1280px responsive layout")

            page.set_viewport_size({"width": 1280, "height": 900})
            page.get_by_role("tab", name="知识点").focus()
            page.keyboard.press("ArrowRight")
            expect(page.get_by_role("tab", name="例题解析")).to_be_focused()
            expect(page.get_by_role("tab", name="例题解析")).to_have_attribute("aria-selected", "true")
            page.locator("#new-chapter").click()
            page.keyboard.press("Escape")
            expect(page.locator("#chapter-dialog")).not_to_be_visible()
            checked.append("keyboard tabs and dialog dismissal")

            # A contract harness exercises WebMCP actions when this browser has no native registry.
            native_webmcp = page.evaluate("Boolean(document.modelContext?.registerTool)")
            if not native_webmcp:
                page.evaluate("""() => {
                    window.contractTools = {};
                    Object.defineProperty(document, 'modelContext', {configurable:true, value: {
                        registerTool(tool) { window.contractTools[tool.name] = tool; }
                    }});
                    registerWebMCP();
                }""")
                registered = page.evaluate("Object.keys(window.contractTools)")
                assert set(registered) == {"list_study_chapters", "open_study_chapter"}
                chapters = page.evaluate("window.contractTools.list_study_chapters.execute({})")
                assert len(chapters) >= 3
                opened = page.evaluate("window.contractTools.open_study_chapter.execute({chapter_id:'demo-network-layer'})")
                assert opened["id"] == "demo-network-layer"
                expect(page.locator("#mode-badge")).to_have_text("教学示例")
                failure = page.evaluate("""async () => { try {
                    await window.contractTools.open_study_chapter.execute({chapter_id:'does-not-exist'});
                    return false;
                } catch { return true; } }""")
                assert failure
                checked.append("WebMCP contract harness: registration, state changes and invalid input; native context unavailable")

            page.evaluate("document.documentElement.style.fontSize = '32px'")
            assert page.evaluate("getComputedStyle(document.documentElement).fontSize") == '32px'
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "horizontal overflow at 200% text size"
            page.screenshot(path=str(OUTPUT / "text-200-percent.png"), full_page=True)
            page.evaluate("document.documentElement.style.fontSize = ''")
            checked.append("200% text enlargement")
            assert not errors, errors
            browser.close()
        result = {"passed": checked, "javascript_errors": errors, "provider": "deterministic local test double; not a real AI model"}
        (OUTPUT / "browser-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == "__main__":
    run()
