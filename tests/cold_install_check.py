"""Verify runtime-only installation and restart durability using the real launcher."""
import argparse
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'test-results'


def run(interpreter):
    OUTPUT.mkdir(exist_ok=True)
    data = tempfile.TemporaryDirectory(prefix='zhizhang-cold-')
    resolved_data = Path(data.name).resolve()
    assert resolved_data.parent == Path(tempfile.gettempdir()).resolve() and resolved_data.name.startswith('zhizhang-cold-')
    environment = os.environ.copy()
    environment['STUDY_DATA_DIR'] = data.name
    environment['PYTHONUTF8'] = '1'
    for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
        environment.pop(key, None)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = None
    log = (OUTPUT / 'cold-install-server.log').open('w', encoding='utf-8')
    checked = []

    def request(path, value=None, method='GET'):
        content = None if value is None else json.dumps(value).encode('utf-8')
        req = urllib.request.Request(url + path, data=content, method=method, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read()

    def launch():
        child = subprocess.Popen([str(interpreter), 'start.py', '--no-browser', '--port', str(port)], cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError(f'Launcher exited with {child.returncode}; see cold-install-server.log')
            try:
                if json.loads(request('/api/health'))['status'] == 'ok':
                    return child
            except (OSError, ValueError):
                time.sleep(.1)
        child.kill()
        raise RuntimeError('Cold-start readiness timed out')

    def stop(child):
        if child and child.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=True)
            else:
                child.terminate()
            child.wait(timeout=10)

    try:
        process = launch()
        assert b'<!doctype html>' in request('/')
        assert json.loads(request('/api/settings'))['configured'] is False
        chapter = json.loads(request('/api/chapters/demo-network-layer'))
        assert len(chapter['guide']['knowledge_points']) == 4
        checked.append('runtime-only clean installation starts the real application')
        note = '干净安装验证：这条笔记应该在重启后保留。'
        result = json.loads(request('/api/chapters/demo-network-layer/notes', {'notes': note, 'expected_revision': chapter['notes_revision']}, 'PUT'))
        assert result['saved']
        request('/api/chapters/demo-network-layer/attempts', {'question_index': 0, 'choice': 0}, 'POST')
        duplicate = subprocess.run([str(interpreter), 'start.py', '--no-browser', '--port', str(port)], cwd=ROOT, env=environment, capture_output=True, timeout=15, creationflags=flags)
        assert duplicate.returncode == 0 and b'already running' in duplicate.stdout, duplicate.stdout + duplicate.stderr
        checked.append('a second launcher reuses the running instance')
        stop(process)
        process = None
        process = launch()
        restored = json.loads(request('/api/chapters/demo-network-layer'))
        assert restored['notes'] == note
        assert restored['attempts'][0]['correct']
        assert note in request('/api/chapters/demo-network-layer/export').decode('utf-8')
        checked.append('notes, attempts and export survive an actual process stop and restart')
        assert note in request('/api/chapters/demo-network-layer/print').decode('utf-8')
        assert b'@page' in request('/handout.css')
        assert b'window.print()' in request('/handout.js')
        backup = json.loads(request('/api/backups/export'))
        assert backup['application'] == 'zhizhang' and backup['chapters'][0]['notes'] == note
        checked.append('printable handout, print assets and learning backup work with runtime dependencies alone')
        course = json.loads(request('/api/course/network'))
        assert len(course['lessons']) == 24 and len(course['units']) == 8
        lesson_id = course['lessons'][0]['id']
        opened = json.loads(request('/api/course/network/lessons/' + lesson_id + '/open', method='POST'))
        assert opened['chapter_id'] == lesson_id
        lesson = json.loads(request('/api/chapters/' + lesson_id))
        assert len(lesson['guide']['quiz']) == 3
        assert b'network-lab-form' in request('/course.js')
        crc = json.loads(request('/api/course/network/labs/crc', {'data': '1101', 'generator': '1011'}, 'POST'))
        assert crc['remainder'] == '001'
        exam = json.loads(request('/api/course/network/exams/2/open', method='POST'))
        assert len(json.loads(request('/api/chapters/' + exam['chapter_id']))['guide']['quiz']) == 12
        checked.append('all 24 course entries, lesson opening, course assets, CRC and comprehensive practice are included in the release')
        request('/api/chapters/' + lesson_id + '/attempts', {'question_index': 0, 'choice': lesson['guide']['quiz'][0]['answer']}, 'POST')
        assert json.loads(request('/api/rewards'))['points'] == 10
        assert b'growth-badges' in request('/rewards.js')
        stop(process)
        process = None
        process = launch()
        assert json.loads(request('/api/rewards'))['points'] == 10
        result = json.loads(request('/api/chapters/' + lesson_id + '/attempts', {'question_index': 0, 'choice': lesson['guide']['quiz'][0]['answer']}, 'POST'))
        assert result['reward_points'] == 0
        checked.append('earned points survive a real process restart and repeated answers do not earn again')
        backup = json.loads(request('/api/backups/export'))
        assert backup['format_version'] == 2 and len(backup['rewards']) == 1 and len(backup['practice_days']) == 1
        with closing(sqlite3.connect(resolved_data / 'study.sqlite3')) as connection:
            connection.execute('DELETE FROM learning_rewards')
            connection.execute('DELETE FROM learning_days')
            connection.executemany('INSERT INTO messages(chapter_id,question,answer,created_at) VALUES (?,?,?,?)',
                [('demo-network-layer', f'历史问题 {i}', f'历史回答 {i}', datetime.now(timezone.utc).isoformat()) for i in range(36)])
            connection.commit()
        boundary = 'zhizhang-cold-install-backup-boundary'
        body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="growth.json"\r\nContent-Type: application/json\r\n\r\n').encode() + json.dumps(backup).encode() + ('\r\n--' + boundary + '--\r\n').encode()
        def restore():
            req = urllib.request.Request(url + '/api/backups/import', data=body,
                headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}, method='POST')
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        assert restore()['points_added'] == 10
        assert restore()['points_added'] == 0
        assert json.loads(request('/api/rewards'))['points'] == 10
        assert json.loads(request('/api/rewards'))['next_practice'] is not None
        checked.append('version-two backup restores missing growth once and daily practice is available in the extracted application')
        study = json.loads(request('/api/chapters/demo-network-layer/study'))
        assert len(study['messages']) == 30 and study['messages_has_more']
        older = json.loads(request('/api/chapters/demo-network-layer/messages?before=' + str(study['messages_cursor'])))
        assert len(older['messages']) == 6 and not older['messages_has_more']
        assert len(json.loads(request('/api/chapters/demo-network-layer'))['messages']) == 36
        checked.append('study pagination reads 30 recent messages while older pages and full detail retain all records')
        micro = json.loads(request('/api/course/microclass'))
        assert len(micro['units']) == 3 and len(micro['lessons']) == 12
        assert sum(item['questions'] for item in micro['lessons']) == 120
        micro_id = micro['lessons'][0]['id']
        request('/api/course/microclass/lessons/' + micro_id + '/open', method='POST')
        chapter = json.loads(request('/api/chapters/' + micro_id + '/study'))
        assert len(chapter['guide']['knowledge_points']) == 6 and len(chapter['guide']['worked_examples']) == 4
        request('/api/chapters/' + micro_id + '/notes', {'notes': '微课堂干净安装笔记', 'expected_revision': 0}, 'PUT')
        request('/api/chapters/' + micro_id + '/attempts', {'question_index': 9, 'choice': chapter['guide']['quiz'][9]['answer']}, 'POST')
        assert json.loads(request('/api/rewards'))['points'] == 20
        stop(process)
        process = None
        process = launch()
        chapter = json.loads(request('/api/chapters/' + micro_id + '/study'))
        assert chapter['notes'] == '微课堂干净安装笔记' and chapter['attempts'][0]['correct']
        assert json.loads(request('/api/rewards'))['points'] == 20
        assert any(item['event_key'].startswith('answer:micro-v1-') for item in json.loads(request('/api/backups/export'))['rewards'])
        checked.append('companion course packages twelve lessons and 120 questions; its notes, tenth answer and rewards survive process restart and export')
        report = {'passed': checked, 'runtime_python': str(interpreter), 'external_model_used': False}
        (OUTPUT / 'cold-install-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        stop(process)
        log.close()
        data.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', type=Path, required=True)
    args = parser.parse_args()
    run(args.python.resolve())
