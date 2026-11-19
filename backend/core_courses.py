"""New core courses reuse the existing chapter and attempt contracts."""
from functools import lru_cache

from . import content_os, content_organization, content_structures
from .models import StudyGuide
from .storage import db, dumps, now

MODULES = {'os': content_os, 'organization': content_organization, 'data-structures': content_structures}
TITLES = {'os': '操作系统', 'organization': '计算机组成原理', 'data-structures': '数据结构'}


@lru_cache(maxsize=3)
def validated_lessons(course_id):
    if course_id not in MODULES:
        raise ValueError('课程不存在。')
    result = {}
    for item in MODULES[course_id].LESSONS:
        StudyGuide.model_validate(item['guide'])
        if item['id'] in result:
            raise ValueError('课时 ID 重复。')
        result[item['id']] = item
    return result


def catalog(course_id):
    lessons = validated_lessons(course_id)
    with db() as conn:
        rows = {row['id']: dict(row) for row in conn.execute('''SELECT c.id,c.completed,
            (SELECT COUNT(*) FROM attempts a WHERE a.chapter_id=c.id) attempted,
            (SELECT COUNT(*) FROM attempts a WHERE a.chapter_id=c.id AND a.correct=1) correct
            FROM chapters c''')}
    return {'title': TITLES[course_id] + ' · 核心导学', 'version': 1,
            'description': '本科通用核心导学 · 原创讲解与练习，非指定教材全文或考试大纲。',
            'units': [i['title'] for i in lessons.values()],
            'lessons': [dict(id=i['id'], unit=i['unit'], number=i['number'], title=i['title'],
                overview=i['guide']['overview'], minutes=i['minutes'], questions=len(i['guide']['quiz']),
                topics=[p['title'] for p in i['guide']['knowledge_points']], started=i['id'] in rows,
                completed=bool(rows.get(i['id'], {}).get('completed')),
                attempted=rows.get(i['id'], {}).get('attempted', 0),
                correct=rows.get(i['id'], {}).get('correct', 0)) for i in lessons.values()]}


def open_lesson(course_id, lesson_id):
    item = validated_lessons(course_id).get(lesson_id)
    if not item:
        raise ValueError('本课程中没有这个课时。')
    # Never replace an existing chapter, including an edited/imported collision.
    with db() as conn:
        conn.execute('''INSERT OR IGNORE INTO chapters
            (id,course,textbook,title,mode,goal,guide,sources,created_at) VALUES (?,?,?,?,?,?,?,?,?)''',
            (lesson_id, TITLES[course_id], '知章原创核心导学 v1', item['title'], 'demo', 'understand',
             dumps(item['guide']), dumps(item['sources']), now()))
    return {'chapter_id': lesson_id}
