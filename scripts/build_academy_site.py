"""Copy the verified shared browser edition into an explicit Sites checkout.

The destination has its own Sites identity. No database, keys, or local data are copied.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ('index.html', 'academy.css', 'academy.mjs', 'adapters.mjs', 'lesson-view.mjs',
          'engines.mjs', 'network.mjs', 'mark.svg', 'courses.json')


def build(destination):
    destination = destination.resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise SystemExit('Choose a separate Site checkout, outside the application repository.')
    manifest = destination / '.openai' / 'hosting.json'
    config = json.loads(manifest.read_text(encoding='utf-8'))
    if not config.get('project_id') or config.get('static', {}).get('directory') != 'dist':
        raise SystemExit('The destination must contain its registered static Sites manifest.')
    output = destination / 'dist'
    output.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copy2(ROOT / 'dist' / 'academy' / name, output / name)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    info = {'source_repository': 'https://github.com/feng0sp/learning-assistant', 'source_commit': commit,
            'source_dirty': dirty, 'course_sha256': hashlib.sha256((output / 'courses.json').read_bytes()).hexdigest(),
            'assets': list(ASSETS)}
    (output / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    (destination / 'README.md').write_text(
        '# 知章公开课程与实验\n\n此目录由 learning-assistant 的共享浏览器模块生成。\n'
        '请在 GitHub 主项目修改源代码并运行构建脚本，避免维护第二套课程或实验实现。\n'
        '源码版本与课程摘要见 dist/build-info.json。\n', encoding='utf-8')
    print(json.dumps({'destination': str(destination), 'source_commit': commit, 'source_dirty': dirty, 'asset_count': len(ASSETS)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', required=True, type=Path)
    build(parser.parse_args().destination)
