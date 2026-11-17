"""Portable, versioned learning backups. Imports are additive and transactional."""
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .ai import validate_guide
from .models import StudyGuide
from .rewards import course_question_keys, reward_spec
from .curriculum import reward_capacity
from .storage import db, now

MAX_BACKUP_BYTES = 30 * 1024 * 1024


class BackupError(Exception):
    pass


class Record(BaseModel):
    model_config = {'extra': 'forbid'}


class TimestampRecord(Record):
    created_at: str

    @field_validator('created_at')
    @classmethod
    def timestamp(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError('timestamp needs a timezone')
        return parsed.astimezone(timezone.utc).isoformat()


class BackupSource(Record):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=90000)


class BackupAttempt(TimestampRecord):
    question_index: int = Field(ge=0, strict=True)
    choice: int = Field(ge=0, le=3, strict=True)
    correct: bool = Field(strict=True)


class BackupMessage(TimestampRecord):
    question: str = Field(min_length=1, max_length=3000)
    answer: str = Field(min_length=1, max_length=100000)


class BackupChapter(TimestampRecord):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    course: str = Field(min_length=1, max_length=100)
    textbook: str = Field(max_length=300)
    title: str = Field(min_length=1, max_length=200)
    mode: Literal['demo', 'material', 'topic']
    goal: Literal['understand', 'exam', 'practice']
    guide: StudyGuide
    sources: list[BackupSource] = Field(max_length=1000)
    notes: str = Field(max_length=30000)
    completed: bool = Field(strict=True)
    attempts: list[BackupAttempt] = Field(max_length=12)
    messages: list[BackupMessage] = Field(max_length=5000)

    @model_validator(mode='after')
    def consistent_learning_content(self):
        if not self.course.strip() or not self.title.strip():
            raise ValueError('blank chapter metadata')
        sources = [source.model_dump() for source in self.sources]
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError('duplicate source identifiers')
        if sum(len(source.text) for source in self.sources) > 90000:
            raise ValueError('too much source text')
        if self.mode == 'topic' and self.sources:
            raise ValueError('topic chapters cannot claim original sources')
        validate_guide(self.guide.model_dump(), sources, self.mode != 'topic')
        seen = set()
        for attempt in self.attempts:
            if attempt.question_index in seen or attempt.question_index >= len(self.guide.quiz):
                raise ValueError('duplicate or invalid question index')
            seen.add(attempt.question_index)
            if attempt.correct != (attempt.choice == self.guide.quiz[attempt.question_index].answer):
                raise ValueError('attempt result does not match the question')
        return self


class BackupReward(Record):
    event_key: str = Field(min_length=1, max_length=110)
    points: int = Field(ge=1, le=20, strict=True)
    kind: Literal['answer', 'lesson']
    earned_at: str

    @field_validator('earned_at')
    @classmethod
    def timestamp(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError('reward timestamp needs a timezone')
        return parsed.astimezone(timezone.utc).isoformat()

    @model_validator(mode='after')
    def known_reward(self):
        if reward_spec(self.event_key) != (self.kind, self.points):
            raise ValueError('unknown or inconsistent course reward')
        return self


class BackupPracticeDay(Record):
    day: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    question_key: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def valid_day_and_question(self):
        date.fromisoformat(self.day)
        if self.question_key not in course_question_keys():
            raise ValueError('unknown practice question')
        return self


class BackupDocument(TimestampRecord):
    application: Literal['zhizhang']
    format_version: Literal[1, 2]
    chapters: list[BackupChapter] = Field(max_length=1000)
    rewards: list[BackupReward] = Field(default_factory=list, max_length=reward_capacity())
    practice_days: list[BackupPracticeDay] = Field(default_factory=list, max_length=100000)

    @model_validator(mode='after')
    def distinct_chapters(self):
        ids = [chapter.id for chapter in self.chapters]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate chapter identifiers')
        growth_fields = {'rewards', 'practice_days'}
        if self.format_version == 1 and self.model_fields_set & growth_fields:
            raise ValueError('version one cannot contain growth records')
        if self.format_version == 2 and not growth_fields <= self.model_fields_set:
            raise ValueError('version two must include growth fields')
        reward_keys = [reward.event_key for reward in self.rewards]
        if len(reward_keys) != len(set(reward_keys)):
            raise ValueError('duplicate reward events')
        practice_keys = [(item.day, item.question_key) for item in self.practice_days]
        if len(practice_keys) != len(set(practice_keys)):
            raise ValueError('duplicate practice records')
        return self


def read_chapter_record(conn, row):
    result = {key: row[key] for key in ('id', 'course', 'textbook', 'title', 'mode', 'goal', 'notes', 'created_at')}
    result.update(guide=json.loads(row['guide']), sources=json.loads(row['sources']), completed=bool(row['completed']))
    result['attempts'] = [dict(attempt) for attempt in conn.execute('SELECT question_index,choice,correct,created_at FROM attempts WHERE chapter_id=? ORDER BY question_index', (row['id'],))]
    for attempt in result['attempts']:
        attempt['correct'] = bool(attempt['correct'])
    result['messages'] = [dict(message) for message in conn.execute('SELECT question,answer,created_at FROM messages WHERE chapter_id=? ORDER BY id', (row['id'],))]
    return result


def export_backup():
    with db() as conn:
        # Keep chapters, notes, answers and conversations in the same SQLite snapshot.
        conn.execute('BEGIN')
        chapters = [read_chapter_record(conn, row) for row in conn.execute('SELECT * FROM chapters ORDER BY created_at,id')]
        rewards = [dict(row) for row in conn.execute('SELECT * FROM learning_rewards ORDER BY event_key')]
        practice_days = [dict(row) for row in conn.execute('SELECT * FROM learning_days ORDER BY day,question_key')]
    try:
        document = BackupDocument(application='zhizhang', format_version=2, created_at=now(), chapters=chapters, rewards=rewards, practice_days=practice_days)
    except (ValueError, ValidationError):
        raise BackupError('现有记录超出备份格式限制或内容不完整，请先保留 data 文件夹并检查记录。') from None
    content = document.model_dump_json(indent=2).encode('utf-8')
    if len(content) > MAX_BACKUP_BYTES:
        raise BackupError('学习记录超过 30 MB，暂不支持整份 JSON 备份。请停止程序后复制 data 文件夹备份。')
    return content


def parse_backup(data):
    if not data or len(data) > MAX_BACKUP_BYTES:
        raise BackupError('备份文件不能为空，且不能超过 30 MB。')
    try:
        return BackupDocument.model_validate_json(data.decode('utf-8-sig'))
    except (ValueError, ValidationError, UnicodeError):
        raise BackupError('备份格式不正确、版本不受支持，或内容与作答记录不一致。请使用学习助手导出的完整 JSON 文件。') from None


def fingerprint(chapter):
    # IDs of messages and local note revisions intentionally do not travel in a backup.
    data = chapter.model_dump()
    data['attempts'].sort(key=lambda attempt: attempt['question_index'])
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def import_plan(conn, document):
    plan = []
    for chapter in document.chapters:
        digest = fingerprint(chapter)
        imported = conn.execute('SELECT chapter_id FROM backup_imports WHERE source_id=? AND digest=?', (chapter.id, digest)).fetchone()
        existing = conn.execute('SELECT * FROM chapters WHERE id=?', (chapter.id,)).fetchone()
        if imported:
            action = 'skip'
        elif existing:
            try:
                identical = fingerprint(BackupChapter.model_validate(read_chapter_record(conn, existing))) == digest
            except (ValueError, ValidationError):
                identical = False
            action = 'skip' if identical else 'copy'
        else:
            action = 'new'
        plan.append((chapter, digest, action))
    return plan


def growth_plan(conn, document):
    existing_rewards = {row[0] for row in conn.execute('SELECT event_key FROM learning_rewards')}
    existing_days = {tuple(row) for row in conn.execute('SELECT day,question_key FROM learning_days')}
    return ([item for item in document.rewards if item.event_key not in existing_rewards],
            [item for item in document.practice_days if (item.day, item.question_key) not in existing_days])


def preview_backup(document):
    with db() as conn:
        conn.execute('BEGIN')
        plan = import_plan(conn, document)
        rewards, practice_days = growth_plan(conn, document)
    return {
        'format_version': document.format_version,
        'rewards_new': len(rewards),
        'points_new': sum(item.points for item in rewards),
        'practice_days_new': len(practice_days),
        'total': len(plan),
        'new': sum(action == 'new' for _, _, action in plan),
        'copies': sum(action == 'copy' for _, _, action in plan),
        'skipped': sum(action == 'skip' for _, _, action in plan),
        'chapters': [{'title': chapter.title, 'course': chapter.course, 'action': action} for chapter, _, action in plan[:20]],
    }


def import_backup(document):
    try:
        return merge_backup(document)
    except sqlite3.DatabaseError:
        raise BackupError('导入未完成，本次新增记录已回滚。请检查本机存储状态后重试，现有记录会保留。') from None


def merge_backup(document):
    counts = {'imported': 0, 'copies': 0, 'skipped': 0, 'chapter_ids': [], 'rewards_imported': 0, 'points_added': 0, 'practice_days_imported': 0}
    with db() as conn:
        # Recompute conflicts under a write lock. No existing chapter is overwritten.
        conn.execute('BEGIN IMMEDIATE')
        for chapter, digest, action in import_plan(conn, document):
            if action == 'skip':
                counts['skipped'] += 1
                conn.execute('INSERT OR IGNORE INTO backup_imports (source_id,digest,chapter_id,created_at) VALUES (?,?,?,?)', (chapter.id, digest, chapter.id, now()))
                continue
            target_id = chapter.id if action == 'new' else uuid4().hex
            title = chapter.title if action == 'new' else chapter.title[:194] + '（导入副本）'
            conn.execute('''INSERT INTO chapters (id,course,textbook,title,mode,goal,guide,sources,notes,completed,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (
                target_id, chapter.course, chapter.textbook, title, chapter.mode, chapter.goal,
                chapter.guide.model_dump_json(), json.dumps([source.model_dump() for source in chapter.sources], ensure_ascii=False),
                chapter.notes, int(chapter.completed), chapter.created_at,
            ))
            for attempt in chapter.attempts:
                conn.execute('INSERT INTO attempts VALUES (?,?,?,?,?)', (target_id, attempt.question_index, attempt.choice, int(attempt.correct), attempt.created_at))
            for message in chapter.messages:
                conn.execute('INSERT INTO messages (chapter_id,question,answer,created_at) VALUES (?,?,?,?)', (target_id, message.question, message.answer, message.created_at))
            conn.execute('INSERT INTO backup_imports (source_id,digest,chapter_id,created_at) VALUES (?,?,?,?)', (chapter.id, digest, target_id, now()))
            counts['imported'] += 1
            counts['copies'] += action == 'copy'
            counts['chapter_ids'].append(target_id)
        new_rewards, new_days = growth_plan(conn, document)
        conn.executemany('INSERT INTO learning_rewards (event_key,points,kind,earned_at) VALUES (?,?,?,?)',
                         [(item.event_key, item.points, item.kind, item.earned_at) for item in new_rewards])
        conn.executemany('INSERT INTO learning_days (day,question_key) VALUES (?,?)',
                         [(item.day, item.question_key) for item in new_days])
        counts['rewards_imported'] = len(new_rewards)
        counts['points_added'] = sum(item.points for item in new_rewards)
        counts['practice_days_imported'] = len(new_days)
    return counts
