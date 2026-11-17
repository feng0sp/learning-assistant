"""Run isolated regression programs; each owns a separate temporary database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
MODULES = (
    'test_app', 'network_course_check', 'microclass_check', 'rewards_check', 'reward_backup_check', 'message_page_check',
    'browser_check', 'browser_backups', 'browser_daily_practice', 'browser_microclass', 'browser_growth_completion', 'browser_density',
    'browser_drafts', 'browser_edges', 'browser_handout', 'browser_job_recovery',
    'browser_materials', 'browser_message_pages', 'browser_mobile', 'browser_network_course',
    'browser_notes_race', 'browser_progress_race', 'browser_questions', 'browser_quiz_pending',
    'browser_review', 'browser_reward_backup', 'browser_rewards', 'browser_settings_race',
    'browser_startup', 'large_library_check',
)


def fingerprint():
    paths = sorted((ROOT / 'backend').glob('*.py')) + sorted((ROOT / 'dist').glob('*'))
    return hashlib.sha256(b''.join(path.read_bytes() for path in paths if path.is_file())).hexdigest()


def run(name):
    output = ROOT / 'test-results' / name
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'source_fingerprint': fingerprint(), 'programs': [], 'external_model_used': False}
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def checkpoint():
        report['updated_at'] = datetime.now(timezone.utc).isoformat()
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['source_changed_during_run'] = fingerprint() != report['source_fingerprint']
        temporary = output / 'report.tmp'
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output / 'report.json')

    checkpoint()
    for module in MODULES:
        began = time.monotonic()
        log = output / (module + '.log')
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, '-m', 'tests.' + module], cwd=ROOT, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT, creationflags=flags)
        entry = {'module': module, 'exit_code': result.returncode,
                 'seconds': round(time.monotonic() - began, 2), 'log': log.name}
        report['programs'].append(entry)
        checkpoint()
        print(f'{module}: {"passed" if result.returncode == 0 else "FAILED"}; {entry["seconds"]} seconds', flush=True)
        if result.returncode:
            report['status'] = 'failed'
            checkpoint()
            return 1
    report['status'] = 'passed'
    checkpoint()
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-name', default='regression')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.report_name):
        parser.error('Report name must contain only letters, digits, underscores or hyphens')
    sys.exit(run(args.report_name))
