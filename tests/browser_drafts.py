"""Verify batched draft persistence without losing final input."""
import copy
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-drafts-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from backend.sample import DEMO_GUIDE
from backend.storage import db, dumps, now


def run():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    output = Path(__file__).resolve().parent.parent / 'test-results'
    output.mkdir(exist_ok=True)
    checked, errors = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("""(() => {
              const original = Storage.prototype.setItem;
              window.draftWrites = 0;
              Storage.prototype.setItem = function(key, value) {
                if (key === 'zhizhang-draft') window.draftWrites++;
                return original.call(this, key, value);
              };
            })();""")
            page.goto(url)
            expect(page.locator('#chapter-title')).to_contain_text('网络层')
            page.locator('#new-chapter').click()
            result = page.evaluate("""() => {
              const field = document.querySelector('#text-input');
              field.value = '学习资料'.repeat(20000);
              const started = performance.now();
              for (let i = 0; i < 50; i++) {
                field.value += '字';
                field.dispatchEvent(new Event('input', {bubbles:true}));
              }
              return {writes: window.draftWrites, elapsed_ms: performance.now() - started};
            }""")
            assert result['writes'] == 0, result
            page.wait_for_function('() => window.draftWrites === 1')
            assert page.evaluate("JSON.parse(localStorage.getItem('zhizhang-draft')).text.length") == 80050
            checked.append('50 rapid edits of an 80k-character draft produce one storage write after typing pauses')
            page.locator('#title-input').fill('关闭窗口前最后输入的标题')
            page.locator('[data-close="chapter-dialog"]').click()
            page.wait_for_function("() => JSON.parse(localStorage.getItem('zhizhang-draft')).chapter === '关闭窗口前最后输入的标题'")
            page.locator('#new-chapter').click()
            page.locator('#title-input').fill('刷新前最后输入的标题')
            page.reload()
            page.locator('#new-chapter').click()
            expect(page.locator('#title-input')).to_have_value('刷新前最后输入的标题')
            expect(page.locator('#text-input')).to_have_value('学习资料' * 20000 + '字' * 50)
            checked.append('closing the dialog and immediately reloading both flush the latest pending draft')
            assert not errors, errors
            browser.close()
        report = {'passed': checked, 'typing_batch': result, 'javascript_errors': errors}
        (output / 'drafts-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    run()
