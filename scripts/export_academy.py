"""Export original canonical lessons to the shared, model-free browser edition."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.course_registry import public_bundle


def export():
    bundle = public_bundle()
    destination = ROOT / 'dist' / 'academy' / 'courses.json'
    content = json.dumps(bundle, ensure_ascii=False, indent=2) + '\n'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding='utf-8')
    print(f'Exported {len(bundle["subjects"])} subjects, {sum(len(p["lessons"]) for p in bundle["courses"])} lessons to {destination.name}.')


if __name__ == '__main__':
    export()
