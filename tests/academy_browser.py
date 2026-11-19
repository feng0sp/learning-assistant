"""Real browser acceptance for the public edition and isolated local database.

Run: python -m tests.academy_browser
Set ACADEMY_BROWSER_CHANNEL=chrome to use an installed Chrome instead of bundled Chromium.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'test-results' / 'academy'
OUTPUT.mkdir(parents=True, exist_ok=True)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class AcademyBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT / 'dist')))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        channel = os.environ.get('ACADEMY_BROWSER_CHANNEL')
        cls.browser = cls.playwright.chromium.launch(**({'channel': channel} if channel else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.page = self.context.new_page()
        self.errors = []
        self.requests = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda request: self.requests.append(request.url))

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.context.close()

    def go(self, suffix=''):
        self.page.goto(self.base + '/academy/' + suffix)
        expect(self.page.locator('#subject-cards .subject-card')).to_have_count(4)

    def start_lab(self, lab_id):
        self.go('#lab/' + lab_id)
        expect(self.page.locator('#lab-title')).to_have_text({'scheduler':'进程调度实验','cache':'直接映射 Cache','hash':'散列与线性探测'}[lab_id])
        self.page.locator('#prediction').fill('先按规则推导，再用过程核对预测。')
        self.page.locator('#start-experiment').click()

    def finish_trace(self):
        expect(self.page.locator('#playback-controls')).to_be_visible()
        for _ in range(100):
            current, total = [int(s.strip().split()[0]) for s in self.page.locator('#step-position').inner_text().split('/')]
            if current == total:
                return
            self.page.locator('#step-next').click()
            expect(self.page.locator('#step-position')).to_have_text(f'{current + 1} / {total} 步')
        self.fail('Trace did not terminate within acceptance limit')

    def test_public_courses_notes_quiz_progress_and_refresh(self):
        self.go()
        self.page.screenshot(path=str(OUTPUT / 'home.png'), full_page=True)
        for subject, title in [('network', '体系结构'), ('os', '系统调用'), ('organization', '系统层次'), ('data-structures', '复杂度')]:
            self.page.locator(f'#subject-nav [data-subject="{subject}"]').click()
            expect(self.page.locator('#lesson-title')).to_contain_text(title)
        self.page.locator('#lesson-notes').fill('我的数据结构笔记 <script>unsafe()</script>')
        self.page.locator('#next-lesson').click()  # must save before navigating
        self.page.locator('#previous-lesson').click()
        expect(self.page.locator('#lesson-notes')).to_have_value('我的数据结构笔记 <script>unsafe()</script>')
        self.page.locator('[data-quiz="0"] input[value="0"]').check()
        self.page.locator('[data-quiz="0"] button').click()
        expect(self.page.locator('#feedback-0')).to_contain_text('再想一想')
        self.page.locator('#complete-lesson').click()
        expect(self.page.locator('#complete-lesson')).to_contain_text('撤销')
        self.page.reload()
        expect(self.page.locator('#lesson-notes')).to_have_value('我的数据结构笔记 <script>unsafe()</script>')
        expect(self.page.locator('[data-quiz="0"] input[value="0"]')).to_be_checked()
        expect(self.page.locator('#complete-lesson')).to_contain_text('撤销')
        self.assertFalse(any('/api/' in url for url in self.requests))

    def test_network_tasks_fail_then_repair_and_preserve_evidence(self):
        self.go('#lab/network')
        for task in ['mask', 'gateway', 'dns', 'route', 'transfer']:
            self.page.locator('#network-task').select_option(task)
            self.page.locator('#prediction').fill('比较网关、服务器 IP 与域名查询的结果。')
            self.page.locator('#start-experiment').click()
            self.page.locator('[data-probe="web"]').click()
            expect(self.page.locator('.network-result')).not_to_have_class('network-result success')
            self.page.locator('#network-settings details summary').click()
            self.page.locator('#config-prefix').fill('24')
            self.page.locator('#config-gateway').fill('10.38.1.1')
            self.page.locator('#config-dns').fill('10.38.2.53')
            self.page.locator('#config-route-enabled').check()
            self.page.locator('#network-settings [type="submit"]').click()
            self.page.locator('[data-probe="web"]').click()
            expect(self.page.locator('.network-result')).to_have_class('network-result success')
            expect(self.page.locator('#review-summary')).to_contain_text('已验证请求与回复')
        self.page.screenshot(path=str(OUTPUT / 'network.png'), full_page=True)
        self.page.locator('#config-gateway').fill('10.38.1.254')
        self.page.locator('#network-settings [type="submit"]').click()
        self.page.locator('[data-probe="web"]').click()
        expect(self.page.locator('#review-summary')).to_contain_text('尚未验证修复')
        with self.page.expect_download() as event:
            self.page.locator('#download-session').click()
        value=json.loads(Path(event.value.path()).read_text(encoding='utf-8'))
        self.assertFalse(value['verified'])
        self.assertTrue(any(x['kind']=='configuration' for x in value['history']))

    def test_scheduler_complete_pause_rewind_and_report(self):
        self.start_lab('scheduler')
        self.page.locator('#auto-play').click()
        expect(self.page.locator('#auto-play')).to_have_text('暂停')
        expect(self.page.locator('#step-position')).to_have_text('1 / 5 步', timeout=3000)
        self.page.locator('#auto-play').click()
        self.finish_trace()
        expect(self.page.locator('#review-summary')).to_contain_text('3.50')
        self.page.locator('.hint-box summary').click()
        self.page.locator('#next-hint').click()
        self.page.locator('#reflection').fill('B 的最后一段只有一个时间单位。')
        self.page.locator('#save-reflection').click()
        with self.page.expect_download() as event:
            self.page.locator('#download-report').click()
        report=Path(event.value.path()).read_text(encoding='utf-8')
        self.assertIn('3.50', report)
        self.assertIn('B 的最后一段', report)
        self.assertIn('提示层数：1', report)
        self.page.locator('#step-back').click()
        expect(self.page.locator('#step-position')).to_have_text('4 / 5 步')

    def test_cache_and_hash_manual_results(self):
        self.start_lab('cache')
        self.finish_trace()
        expect(self.page.locator('#review-summary')).to_contain_text('25.0%')
        self.page.screenshot(path=str(OUTPUT / 'cache.png'), full_page=True)
        self.page.locator('#cache-addresses').fill('65536')
        self.page.locator('#cache-addresses').blur()
        self.page.locator('#start-experiment').click()
        expect(self.page.locator('#experiment-error')).to_contain_text('16 位')
        self.start_lab('hash')
        self.finish_trace()
        expect(self.page.locator('#review-summary')).to_contain_text('存入 3 个键')
        self.page.locator('#records-button').click()
        expect(self.page.locator('#record-sessions .record-item')).to_have_count(2)

    def test_storage_disabled_keeps_session_usable_and_exportable(self):
        self.page.add_init_script("Storage.prototype.getItem=()=>{throw new DOMException('Blocked','SecurityError')};Storage.prototype.setItem=()=>{throw new DOMException('Blocked','SecurityError')}")
        self.go('#lesson/os-v1-scheduling')
        expect(self.page.locator('#storage-warning')).to_be_visible()
        self.page.locator('#lesson-notes').fill('临时笔记仍可导出')
        self.page.locator('#save-notes').click()
        expect(self.page.locator('#notes-state')).to_have_text('已保存')
        with self.page.expect_download() as event:
            self.page.locator('#export-data').click()
        value=json.loads(Path(event.value.path()).read_text(encoding='utf-8'))
        self.assertEqual(value['records']['os-v1-scheduling']['notes'], '临时笔记仍可导出')

    def test_multiple_windows_detect_note_conflict_without_erasing_draft(self):
        self.go('#lesson/co-v1-cache')
        other=self.context.new_page()
        other.goto(self.base+'/academy/#lesson/co-v1-cache')
        expect(other.locator('#lesson-notes')).to_be_visible()
        self.page.locator('#lesson-notes').fill('第一窗口版本')
        self.page.locator('#save-notes').click()
        expect(self.page.locator('#notes-state')).to_have_text('已保存')
        other.locator('#lesson-notes').fill('第二窗口草稿')
        other.locator('#save-notes').click()
        expect(other.locator('#notes-error')).to_contain_text('另一窗口')
        expect(other.locator('#lesson-notes')).to_have_value('第二窗口草稿')
        with other.expect_download() as event:
            other.locator('#download-draft').click()
        self.assertEqual(Path(event.value.path()).read_text(encoding='utf-8'), '第二窗口草稿')
        other.locator('#reload-notes').click()
        expect(other.locator('#lesson-notes')).to_have_value('第一窗口版本')

    def test_mobile_pages_have_no_document_overflow_and_controls_work(self):
        self.page.set_viewport_size({'width':390,'height':844})
        self.go()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),391)
        self.page.screenshot(path=str(OUTPUT / 'mobile-home.png'), full_page=True)
        self.page.locator('#subject-nav [data-subject="organization"]').click()
        expect(self.page.locator('#lesson-title')).to_contain_text('系统层次')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),391)
        self.page.locator('#outline-lab').click()
        self.page.locator('#prediction').fill('第二次访问应命中。')
        self.page.locator('#start-experiment').click()
        self.page.locator('#step-next').click()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),391)
        self.page.screenshot(path=str(OUTPUT / 'mobile-cache.png'), full_page=True)

    def test_corrupt_storage_is_preserved_and_keyboard_skip_link_works(self):
        self.page.add_init_script("localStorage.setItem('zhizhang-academy-v1','{broken')")
        self.go()
        expect(self.page.locator('#storage-warning')).to_be_visible()
        self.assertEqual(self.page.evaluate("localStorage.getItem('zhizhang-academy-v1')"),'{broken')
        self.page.keyboard.press('Tab')
        expect(self.page.locator('.skip-link')).to_be_focused()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('#main')).to_be_focused()

    def test_experiment_restores_cursor_prediction_and_history_after_reload(self):
        self.start_lab('cache')
        expect(self.page.locator('#playback-controls')).to_be_visible()
        self.page.locator('#step-next').click()
        self.page.locator('#step-next').click()
        expect(self.page.locator('#step-position')).to_have_text('2 / 4 步')
        self.page.reload()
        self.page.locator('#resume-experiment').click()
        expect(self.page.locator('#step-position')).to_have_text('2 / 4 步')
        expect(self.page.locator('#prediction')).to_have_value('先按规则推导，再用过程核对预测。')
        self.finish_trace()
        expect(self.page.locator('#review-summary')).to_contain_text('25.0%')
        self.page.locator('#records-button').click()
        expect(self.page.locator('#record-sessions .record-item')).to_have_count(1)


class LocalAcademyBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='academy-local-browser-')
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        cls.base=f'http://127.0.0.1:{port}'
        env=dict(os.environ,STUDY_DATA_DIR=cls.temp.name)
        for key in ('STUDY_API_BASE_URL','STUDY_API_KEY','STUDY_MODEL'):
            env.pop(key,None)
        cls.process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        for _ in range(100):
            try:
                with urlopen(cls.base+'/api/courses',timeout=.2):break
            except Exception:time.sleep(.1)
        else:raise RuntimeError('Isolated backend did not start')
        cls.playwright=sync_playwright().start()
        channel=os.environ.get('ACADEMY_BROWSER_CHANNEL')
        cls.browser=cls.playwright.chromium.launch(**({'channel':channel} if channel else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop();cls.process.terminate();cls.process.wait(timeout=10);cls.temp.cleanup()

    def test_shared_reader_uses_database_and_old_ui_navigates_new_courses(self):
        context=self.browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        csp_errors=[]
        page.on('console',lambda msg:csp_errors.append(msg.text) if 'violates' in msg.text and 'Content Security Policy' in msg.text else None)
        page.goto(self.base+'/academy/?local=1#lesson/os-v1-scheduling')
        expect(page.locator('#storage-mode')).to_contain_text('本地完整版')
        page.locator('#lesson-notes').fill('SQLite 中的调度笔记')
        page.locator('#next-lesson').click()
        page.locator('#previous-lesson').click()
        expect(page.locator('#lesson-notes')).to_have_value('SQLite 中的调度笔记')
        stored=page.request.get(self.base+'/api/chapters/os-v1-scheduling').json()
        self.assertEqual(stored['notes'],'SQLite 中的调度笔记')
        self.assertGreater(stored['notes_revision'],0)
        page.locator('#outline-lab').click()
        page.locator('#prediction').fill('先按到达顺序观察调度。')
        page.locator('#start-experiment').click()
        expect(page.locator('#playback-controls')).to_be_visible()
        page.locator('#step-next').click()
        expect(page.locator('#step-position')).to_have_text('1 / 5 步')
        self.assertEqual(csp_errors,[])
        page.goto(self.base+'/')
        page.locator('#open-course').click()
        expect(page.locator('[data-course-catalog]')).to_have_count(5)
        page.locator('[data-course-catalog="os"]').click()
        expect(page.locator('#course-lessons .course-lesson')).to_have_count(8)
        page.locator('#course-lessons [data-network-lesson="os-v1-system-calls"]').click()
        expect(page.locator('#chapter-title')).to_contain_text('系统调用')
        page.locator('#course-next').click()
        expect(page.locator('#chapter-title')).to_contain_text('进程、线程')
        self.assertEqual(errors,[])
        context.close()


if __name__=='__main__':
    unittest.main(verbosity=2)
