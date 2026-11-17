"""Long-lived browser session check without page reloads; isolated local data only."""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone

from playwright.sync_api import expect, sync_playwright

from tests.endurance_check import LocalServer, OUTPUT, TEST_DIRECTORY, FakeProvider
from backend.storage import db, now


def run(until, report_name='session-report'):
    provider = FakeProvider().start()
    server = LocalServer()
    started = datetime.now(timezone.utc)
    files = sorted((OUTPUT.parent / 'backend').glob('*.py')) + sorted((OUTPUT.parent / 'dist').glob('*'))
    def fingerprint():
        return hashlib.sha256(b''.join(p.read_bytes() for p in files if p.is_file())).hexdigest()
    report = {'status': 'running', 'started_at': started.isoformat(), 'scheduled_end': until.isoformat(), 'cycles': [], 'errors': [], 'real_model_used': False, 'source_fingerprint': fingerprint(), 'page_reloads': 0}
    output = OUTPUT / (report_name + '.json')
    def checkpoint():
        report['updated_at'] = datetime.now(timezone.utc).isoformat()
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output)
    try:
        with db() as conn:
            conn.execute("INSERT INTO chapters (id,course,textbook,title,mode,goal,guide,sources,notes,created_at) SELECT 'session-second',course,textbook,'持续会话的第二章',mode,goal,guide,sources,'',created_at FROM chapters WHERE id='demo-network-layer'")
            conn.executemany('INSERT INTO messages (chapter_id,question,answer,created_at) VALUES (?,?,?,?)', [(chapter, f'已有问题 {i}', f'已有回答 {i}，供浏览会话测试。', now()) for chapter in ('demo-network-layer', 'session-second') for i in range(40)])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            page.request.put(server.url + '/api/settings', data={'base_url': provider.base_url, 'model': 'session-test-double', 'api_key': 'test-only'})
            page.goto(server.url)
            expect(page.locator('#chapter-title')).not_to_have_text('正在打开学习空间…')
            session = page.context.new_cdp_session(page)
            session.send('Performance.enable')
            drafts = {}
            cycle = 0
            checkpoint()
            while datetime.now(timezone.utc) < until:
                cycle += 1
                began = time.monotonic()
                chapter_id = 'demo-network-layer' if cycle % 2 else 'session-second'
                if page.viewport_size['width'] <= 720:
                    if page.locator('#toggle-navigation').get_attribute('aria-expanded') == 'false':
                        page.locator('#toggle-navigation').click()
                page.locator(f'[data-chapter="{chapter_id}"]').click()
                page.wait_for_function('(id) => state.chapter.id === id', arg=chapter_id)
                expect(page.locator('#question')).to_have_value(drafts.get(chapter_id, ''))
                question = f'第 {cycle} 轮会话问题'
                page.locator('#question').fill(question)
                drafts[chapter_id] = question
                if cycle % 5 == 0:
                    page.locator('#ask-button').click()
                    expect(page.locator('#ask-button')).to_be_enabled()
                    expect(page.locator('#question')).to_have_value('')
                    drafts[chapter_id] = ''
                note = f'无刷新会话第 {cycle} 次笔记；章节 {chapter_id}。'
                page.locator('#notes').fill(note)
                page.locator('#save-notes').click()
                expect(page.locator('#notes-status')).to_have_text('已保存到本机')
                page.get_by_role('tab', name='自测练习').click()
                page.locator(f'input[name="quiz-0"][value="{cycle % 4}"]').check()
                page.locator('[data-check="0"]').click()
                expect(page.locator('#quiz-0 .quiz-feedback')).to_be_visible()
                page.get_by_role('tab', name='例题解析').click()
                page.locator('.example-card summary').first.click()
                expect(page.locator('.answer-box').first).to_be_visible()
                page.get_by_role('tab', name='知识点').click()
                expect(page.locator('#messages .answer')).to_have_count(30)
                if cycle % 6 == 0:
                    page.locator('#older-messages').click()
                    expect(page.locator('#messages .answer').nth(30)).to_be_attached()
                saved = page.request.get(server.url + '/api/chapters/' + chapter_id).json()
                assert saved['notes'] == note
                assert any(item['question_index'] == 0 and item['choice'] == cycle % 4 for item in saved['attempts'])
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                metrics = {m['name']: m['value'] for m in session.send('Performance.getMetrics')['metrics']}
                counters = session.send('Memory.getDOMCounters')
                visible_nodes = page.evaluate('document.querySelectorAll("*").length')
                assert visible_nodes < 1200, visible_nodes
                assert not report['errors'], report['errors']
                report['cycles'].append({'cycle': cycle, 'at': datetime.now(timezone.utc).isoformat(), 'duration_seconds': round(time.monotonic() - began, 3), 'js_heap_used_mb': round(metrics['JSHeapUsedSize'] / 1024**2, 3), 'visible_nodes': visible_nodes, 'dom_counters': counters})
                checkpoint()
                print(f'Session cycle {cycle}: passed; nodes={visible_nodes}; heap={report["cycles"][-1]["js_heap_used_mb"]} MB', flush=True)
                if cycle % 4 == 0:
                    page.set_viewport_size({'width': 390 if page.viewport_size['width'] == 1280 else 1280, 'height': 900})
                delay = min(20, max(0, (until - datetime.now(timezone.utc)).total_seconds()))
                page.wait_for_timeout(delay * 1000)
            browser.close()
        report['status'] = 'passed'
        report['elapsed_seconds'] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
        report['source_changed_during_run'] = fingerprint() != report['source_fingerprint']
        checkpoint()
    except BaseException as exc:
        report['status'] = 'failed'
        report['errors'].append(f'{type(exc).__name__}: {exc}')
        checkpoint()
        raise
    finally:
        report['elapsed_seconds'] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
        report['source_changed_during_run'] = fingerprint() != report['source_fingerprint']
        checkpoint()
        server.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--until-utc', required=True)
    parser.add_argument('--report-name', default='session-report')
    args = parser.parse_args()
    until = datetime.fromisoformat(args.until_utc)
    if until.tzinfo is None or until <= datetime.now(timezone.utc):
        parser.error('Use a future timezone-aware deadline')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.report_name):
        parser.error('Report name must contain only letters, digits, underscores or hyphens')
    run(until, args.report_name)
