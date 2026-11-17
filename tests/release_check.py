"""Verify the distributed ZIP, then launch its extracted copy with runtime-only Python."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent


def run(archive_path, interpreter):
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    forbidden = {'data', '.venv', '.git', '__pycache__', 'test-results', '.env'}
    with tempfile.TemporaryDirectory(prefix='zhizhang-release-') as temporary:
        destination = Path(temporary).resolve()
        assert destination.parent == Path(tempfile.gettempdir()).resolve()
        assert destination.name.startswith('zhizhang-release-')
        with ZipFile(archive_path) as archive:
            assert archive.testzip() is None, 'ZIP checksum failure'
            names = archive.namelist()
            assert len(names) == len(set(names)), 'Duplicate archive entries'
            for name in names:
                target = (destination / name).resolve()
                assert target.is_relative_to(destination), 'Unsafe archive path'
                assert not forbidden.intersection(Path(name).parts), 'Runtime data in release'
                assert not name.endswith('settings.json'), 'Settings file in release'
            archive.extractall(destination)
        application = destination / '知章学习助手'
        manifest_path = application / 'SOURCE_MANIFEST.sha256'
        manifest_files = set()
        for line in manifest_path.read_text(encoding='utf-8').splitlines():
            expected, relative = line.split('  ', 1)
            source = (application / relative).resolve()
            assert source.is_relative_to(application), 'Unsafe manifest path'
            assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, relative
            manifest_files.add(source)
        extracted_files = {file.resolve() for file in application.rglob('*') if file.is_file()}
        assert extracted_files == manifest_files | {manifest_path.resolve()}, 'Incomplete manifest'
        environment = os.environ.copy()
        environment['PYTHONUTF8'] = '1'
        # The extracted project and clean interpreter must stand on their own.
        environment.pop('PYTHONPATH', None)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        result = subprocess.run(
            [str(interpreter), '-m', 'tests.cold_install_check', '--python', str(interpreter)],
            cwd=application, env=environment, capture_output=True, text=True,
            encoding='utf-8', timeout=120, creationflags=flags,
        )
        (output / 'release-check.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        assert result.returncode == 0, 'Extracted application failed; see release-check.log'
        cold = json.loads((application / 'test-results' / 'cold-install-report.json').read_text(encoding='utf-8'))
        report = {
            'status': 'passed',
            'archive_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            'verified_source_files': len(manifest_files),
            'passed': ['ZIP integrity, source manifest and exclusion of runtime data', *cold['passed']],
            'external_model_used': False,
        }
    (output / 'release-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    args = parser.parse_args()
    run(args.archive.resolve(), args.python.resolve())
