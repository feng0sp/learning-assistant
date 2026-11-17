"""Small local achievements; reward each course question and milestone once."""
from datetime import datetime
import json
from functools import lru_cache

from .curriculum import all_lessons as validated_lessons, course_id
from .network_course import validated_lessons as network_lessons
from .storage import db, now


@lru_cache(maxsize=1)
def course_question_keys():
    return frozenset(f"{chapter_id}:{index}" for chapter_id, lesson in validated_lessons().items() for index in range(len(lesson['guide']['quiz'])))


def reward_spec(event_key):
    if event_key.startswith('answer:') and event_key[7:] in course_question_keys():
        return 'answer', 10
    if event_key.startswith('lesson:') and event_key[7:] in validated_lessons():
        return 'lesson', 20
    return None


def award_answer(conn, chapter, index, correct):
    lesson = validated_lessons().get(chapter['id'])
    if not correct or not lesson or chapter['guide']['quiz'] != lesson['guide']['quiz']:
        return 0
    day = datetime.now().astimezone().date().isoformat()
    question_key = f"{chapter['id']}:{index}"
    conn.execute('INSERT OR IGNORE INTO learning_days VALUES (?,?)', (day, question_key))
    gained = 0
    cursor = conn.execute('INSERT OR IGNORE INTO learning_rewards VALUES (?,?,?,?)',
                          ('answer:' + question_key, 10, 'answer', now()))
    if cursor.rowcount:
        gained += 10
    count = conn.execute('SELECT COUNT(*) FROM attempts WHERE chapter_id=? AND correct=1', (chapter['id'],)).fetchone()[0]
    if count == len(lesson['guide']['quiz']):
        cursor = conn.execute('INSERT OR IGNORE INTO learning_rewards VALUES (?,?,?,?)',
                              ('lesson:' + chapter['id'], 20, 'lesson', now()))
        if cursor.rowcount:
            gained += 20
    return gained


def summary():
    day = datetime.now().astimezone().date().isoformat()
    with db() as conn:
        conn.execute('BEGIN')
        points = conn.execute('SELECT COALESCE(SUM(points),0) FROM learning_rewards').fetchone()[0]
        lessons = conn.execute("SELECT COUNT(*) FROM learning_rewards WHERE kind='lesson'").fetchone()[0]
        answers = conn.execute("SELECT COUNT(*) FROM learning_rewards WHERE kind='answer'").fetchone()[0]
        legacy = set(network_lessons())
        original_lessons = sum(row[0][7:] in legacy for row in conn.execute("SELECT event_key FROM learning_rewards WHERE kind='lesson'"))
        original_answers = sum(row[0][7:].rsplit(':', 1)[0] in legacy for row in conn.execute("SELECT event_key FROM learning_rewards WHERE kind='answer'"))
        today = conn.execute('SELECT COUNT(*) FROM learning_days WHERE day=?', (day,)).fetchone()[0]
        practice = next_practice(conn, day)
    levels = [(0, '探索新知'), (100, '渐入佳境'), (300, '融会贯通'), (600, '网络达人'), (1200, '学有所成')]
    level = max(i for i, (threshold, _) in enumerate(levels) if points >= threshold)
    badges = [('first', '迈出第一步', '首次答对一道原完整课程题', original_answers >= 1),
              ('lesson', '理解一课', '原完整课程一课三题全部答对', original_lessons >= 1),
              ('six', '稳步积累', '原完整课程六课获得全对奖励', original_lessons >= 6),
              ('half', '学程过半', '原完整课程十二课获得全对奖励', original_lessons >= 12),
              ('all', '贯通网络', '原完整课程二十四课获得全对奖励', original_lessons >= 24)]
    return {'points': points, 'level': level + 1, 'title': levels[level][1], 'level_start': levels[level][0],
            'next_level': levels[level + 1][0] if level + 1 < len(levels) else None,
            'today': today, 'daily_goal': 3, 'day': day, 'lessons': lessons, 'next_practice': practice,
            'badges': [dict(id=i, title=t, description=d, earned=e) for i, t, d, e in badges]}


def next_practice(conn, day):
    completed_today = {row[0] for row in conn.execute('SELECT question_key FROM learning_days WHERE day=?', (day,))}
    lesson_ids = tuple(validated_lessons())
    placeholders = ','.join('?' for _ in lesson_ids)
    answered = {(row['chapter_id'], row['question_index']): bool(row['correct']) for row in conn.execute(
        f'SELECT chapter_id,question_index,correct FROM attempts WHERE chapter_id IN ({placeholders})', lesson_ids)}
    earned = {row[0][7:] for row in conn.execute("SELECT event_key FROM learning_rewards WHERE kind='answer'")}
    saved = {row['id']: row['guide'] for row in conn.execute(f'SELECT id,guide FROM chapters WHERE id IN ({placeholders})', lesson_ids)}
    candidates = []
    for chapter_id, lesson in validated_lessons().items():
        # A user-imported chapter may share an ID but contain different questions.
        if chapter_id in saved and json.loads(saved[chapter_id]).get('quiz') != lesson['guide']['quiz']:
            continue
        for index in range(len(lesson['guide']['quiz'])):
            key = f'{chapter_id}:{index}'
            wrong = answered.get((chapter_id, index)) is False
            if key in completed_today and not wrong:
                continue
            reason = '先巩固这道错题' if wrong else ('试试这道未作答的题' if (chapter_id, index) not in answered else '再巩固一道课程题')
            course = course_id(chapter_id)
            candidate = {'chapter_id': chapter_id, 'course_id': course, 'question_index': index, 'title': lesson['title'], 'reason': reason}
            candidates.append(((not wrong, key in earned, course != 'network', lesson['number'], index), candidate))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None
