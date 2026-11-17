"""Create a source release from an explicit allowlist. Never include data or keys."""
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent


def build():
    files = [ROOT / name for name in (
        'README.md', '.gitignore', 'start.py', '启动学习助手.bat',
        'requirements.txt', 'requirements.lock.txt', 'requirements-dev.txt',
    )]
    for folder, pattern in (('backend', '*.py'), ('dist', '*'), ('docs', '*.md'), ('examples', '*.md'), ('tests', '*.py'), ('scripts', '*.py')):
        files.extend(file for file in (ROOT / folder).glob(pattern) if file.is_file())
    files = sorted(set(files))
    target = ROOT / 'release' / '知章学习助手.zip'
    target.parent.mkdir(exist_ok=True)
    manifest = []
    with ZipFile(target, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for file in files:
            relative = file.relative_to(ROOT).as_posix()
            assert not set(file.relative_to(ROOT).parts) & {'data', '.venv', 'test-results', '__pycache__'}
            content = file.read_bytes()
            archive.writestr('知章学习助手/' + relative, content)
            manifest.append(hashlib.sha256(content).hexdigest() + '  ' + relative)
        archive.writestr('知章学习助手/SOURCE_MANIFEST.sha256', '\n'.join(manifest) + '\n')
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        assert not any('/data/' in name or name.endswith('settings.json') for name in archive.namelist())
    print(f'Release contains {len(files)} source files; {target.stat().st_size} bytes.')
    print(str(target))
    return target


if __name__ == '__main__':
    build()
