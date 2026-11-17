"""Long-running growth/backup checks with real subprocess restarts and isolated data."""
import argparse
from contextlib import closing
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'test-results'


def fingerprint():
    paths = sorted((ROOT / 'backend').glob('*.py')) + sorted((ROOT / 'dist').glob('*'))
    return hashlib.sha256(b''.join(p.read_bytes() for p in paths if p.is_file())).hexdigest()


class IsolatedProcess:
    def __init__(self, report_name):
        self.directory = tempfile.TemporaryDirectory(prefix='zhizhang-growth-run-')
        self.path = Path(self.directory.name).resolve()
        assert self.path.parent == Path(tempfile.gettempdir()).resolve() and self.path.name.startswith('zhizhang-growth-run-')
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            self.port = reserve.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.environment = os.environ.copy()
        self.environment['STUDY_DATA_DIR'] = str(self.path)
        self.environment['PYTHONUTF8'] = '1'
        for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
            self.environment.pop(key, None)
        self.log = (OUTPUT / f'{report_name}-server.log').open('w', encoding='utf-8')
        self.process = None

    def start(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = subprocess.Popen([sys.executable, 'start.py', '--no-browser', '--port', str(self.port)], cwd=ROOT,
                                        env=self.environment, stdout=self.log, stderr=subprocess.STDOUT, creationflags=flags)
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError('Isolated launcher exited unexpectedly')
                try:
                    if client.get(self.url + '/api/health').json()['status'] == 'ok':
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(.1)
        raise RuntimeError('Isolated process did not become ready')

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                self.process.terminate()
            self.process.wait(timeout=10)
        self.process = None

    def close(self):
        self.stop()
        self.log.close()
        self.directory.cleanup()


def run(until, interval, restart_every, report_name):
    OUTPUT.mkdir(exist_ok=True)
    started = datetime.now(timezone.utc)
    report = {'status': 'running', 'started_at': started.isoformat(), 'scheduled_end': until.isoformat(),
              'source_fingerprint': fingerprint(), 'cycles': [], 'errors': [], 'real_model_used': False, 'process_restarts': 0}
    output = OUTPUT / (report_name + '.json')
    def checkpoint():
        report['updated_at'] = datetime.now(timezone.utc).isoformat()
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output)
    server = IsolatedProcess(report_name)
    client = None
    checkpoint()
    try:
        server.start()
        client = httpx.Client(base_url=server.url, trust_env=False, timeout=20)
        def api(path, data=None):
            response = client.get(path) if data is None else client.post(path, json=data)
            response.raise_for_status()
            return response.json()
        lessons = api('/api/course/network')['lessons']
        earned_questions, earned_lessons = set(), set()
        expected_points = 0
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            cycle = 0
            while datetime.now(timezone.utc) < until:
                cycle += 1
                began = time.monotonic()
                if cycle > 1 and (cycle - 1) % restart_every == 0:
                    client.close()
                    server.stop()
                    server.start()
                    client = httpx.Client(base_url=server.url, trust_env=False, timeout=20)
                    report['process_restarts'] += 1
                    assert api('/api/rewards')['points'] == expected_points
                lesson = lessons[(cycle - 1) % len(lessons)]
                chapter_id = lesson['id']
                api('/api/course/network/lessons/' + chapter_id + '/open', {})
                chapter = api('/api/chapters/' + chapter_id)
                index = ((cycle - 1) // len(lessons)) % 3
                question_key = (chapter_id, index)
                first = question_key not in earned_questions
                earned_questions.add(question_key)
                bonus = chapter_id not in earned_lessons and all((chapter_id, i) in earned_questions for i in range(3))
                if bonus:
                    earned_lessons.add(chapter_id)
                delta = (10 if first else 0) + (20 if bonus else 0)
                response = api('/api/chapters/' + chapter_id + '/attempts', {'question_index': index, 'choice': chapter['guide']['quiz'][index]['answer']})
                assert response['reward_points'] == delta, response
                expected_points += delta
                retry = api('/api/chapters/' + chapter_id + '/attempts', {'question_index': index, 'choice': chapter['guide']['quiz'][index]['answer']})
                assert retry['reward_points'] == 0
                growth = api('/api/rewards')
                assert growth['points'] == expected_points <= 1200
                assert growth['lessons'] == len(earned_lessons)
                note = f'持续检查第 {cycle} 轮；奖励总分 {expected_points}。'
                saved = client.put('/api/chapters/' + chapter_id + '/notes', json={'notes': note, 'expected_revision': chapter['notes_revision']})
                saved.raise_for_status()
                if cycle % 6 == 0:
                    snapshot = client.get('/api/backups/export')
                    snapshot.raise_for_status()
                    assert snapshot.json()['format_version'] == 2
                    restored = client.post('/api/backups/import', files={'file': ('growth.json', snapshot.content, 'application/json')})
                    restored.raise_for_status()
                    assert restored.json()['points_added'] == 0 and restored.json()['imported'] == 0
                    assert api('/api/chapters/' + chapter_id)['notes'] == note
                if cycle % 9 == 0:
                    invalid = client.post('/api/chapters/' + chapter_id + '/attempts', json={'question_index': 99, 'choice': 0})
                    assert invalid.status_code == 422
                    assert api('/api/rewards')['points'] == expected_points
                page.set_viewport_size({'width': 390 if cycle % 3 == 0 else 1280, 'height': 900})
                page.goto(server.url + '/#chapter/' + chapter_id)
                expect(page.locator('#notes')).to_have_value(note)
                expect(page.locator('#reward-pill-label')).to_contain_text(f'{expected_points} 积分')
                page.locator('#open-rewards').click()
                expect(page.locator('#growth-points')).to_have_text(f'{expected_points} 学习积分')
                assert page.locator('#rewards-dialog').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
                page.locator('[data-close="rewards-dialog"]').click()
                if cycle % 4 == 0:
                    page.locator('#toggle-focus').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                with closing(sqlite3.connect(server.path / 'study.sqlite3')) as connection:
                    integrity = connection.execute('PRAGMA quick_check').fetchone()[0]
                assert integrity == 'ok'
                assert not report['errors'], report['errors']
                report['cycles'].append({'cycle': cycle, 'at': datetime.now(timezone.utc).isoformat(),
                    'duration_seconds': round(time.monotonic() - began, 3), 'points': expected_points,
                    'unique_correct_questions': len(earned_questions), 'rewarded_lessons': len(earned_lessons), 'database_check': integrity})
                checkpoint()
                print(f'Growth cycle {cycle}: passed; points={expected_points}; restarts={report["process_restarts"]}', flush=True)
                delay = min(interval, max(0, (until - datetime.now(timezone.utc)).total_seconds()))
                page.wait_for_timeout(delay * 1000)
            browser.close()
        report['status'] = 'passed'
    except BaseException as error:
        report['status'] = 'failed'
        report['errors'].append(f'{type(error).__name__}: {error}')
        raise
    finally:
        report['elapsed_seconds'] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
        report['source_changed_during_run'] = fingerprint() != report['source_fingerprint']
        checkpoint()
        if client:
            client.close()
        server.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    deadline = parser.add_mutually_exclusive_group(required=True)
    deadline.add_argument('--until-utc')
    deadline.add_argument('--seconds', type=float)
    parser.add_argument('--interval', type=float, default=20)
    parser.add_argument('--restart-every', type=int, default=24)
    parser.add_argument('--report-name', default='rewards-endurance')
    args = parser.parse_args()
    until = datetime.fromisoformat(args.until_utc) if args.until_utc else datetime.now(timezone.utc) + timedelta(seconds=args.seconds)
    if until.tzinfo is None or until <= datetime.now(timezone.utc) or args.interval <= 0 or args.restart_every < 1:
        parser.error('Use a future deadline and positive interval/restart frequency')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.report_name):
        parser.error('Use a simple letters/digits/hyphen/underscore report name')
    run(until, args.interval, args.restart_every, args.report_name)
