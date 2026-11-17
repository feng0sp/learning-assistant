"""Exercise the Windows double-click entry point in a fresh extracted release."""
import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent


def run(archive_path):
    if os.name != 'nt':
        raise RuntimeError('The BAT bootstrap check requires Windows')
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='zhizhang-bootstrap-') as temporary:
        destination = Path(temporary).resolve()
        assert destination.parent == Path(tempfile.gettempdir()).resolve()
        assert destination.name.startswith('zhizhang-bootstrap-')
        # Cover both Unicode and spaces in the containing directory.
        extraction = destination / '首次 启动验证'
        extraction.mkdir()
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                assert (extraction / name).resolve().is_relative_to(extraction)
            archive.extractall(extraction)
        application = extraction / '知章学习助手'
        assert not (application / '.venv').exists()
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        environment = os.environ.copy()
        for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL', 'STUDY_DATA_DIR', 'PYTHONPATH'):
            environment.pop(key, None)
        environment['PYTHONUTF8'] = '1'
        process = None
        with (output / 'bootstrap-check.log').open('w', encoding='utf-8') as log:
            try:
                process = subprocess.Popen(
                    ['cmd.exe', '/d', '/c', '启动学习助手.bat', '--no-browser', '--port', str(port)],
                    cwd=application, env=environment, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                ready = False
                while time.monotonic() - started < 180:
                    if process.poll() is not None:
                        raise RuntimeError('Bootstrap exited; see bootstrap-check.log')
                    try:
                        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
                            ready = json.load(response).get('name') == '知章'
                        if ready:
                            break
                    except (OSError, ValueError):
                        time.sleep(.3)
                assert ready, 'Bootstrap timed out; see bootstrap-check.log'
                assert (application / '.venv' / 'Scripts' / 'python.exe').is_file()
                assert (application / 'data' / 'study.sqlite3').is_file()
                with urllib.request.urlopen(url + '/api/chapters/demo-network-layer', timeout=5) as response:
                    chapter = json.load(response)
                assert chapter['mode'] == 'demo' and len(chapter['guide']['knowledge_points']) == 4
                report = {
                    'status': 'passed',
                    'passed': ['Windows BAT creates its own venv, installs runtime dependencies and starts the extracted application', 'a parent path containing Chinese characters and spaces works', 'the built-in chapter is readable without a model key'],
                    'startup_seconds': round(time.monotonic() - started, 2),
                    'external_model_used': False,
                }
            finally:
                if process and process.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
                    process.wait(timeout=10)
    (output / 'bootstrap-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    run(args.archive.resolve())
