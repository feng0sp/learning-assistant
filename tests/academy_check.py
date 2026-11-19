"""Core curriculum contracts, compatibility, content facts and export consistency."""
import json
import os
from pathlib import Path
import tempfile
import unittest

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-academy-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient
from backend.main import app
from backend import course_registry, core_courses
from backend.curriculum import course_id, reward_capacity
from backend.models import StudyGuide
from backend.storage import db


class AcademyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        TEST_DIRECTORY.cleanup()

    def setUp(self):
        with db() as conn:
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
            conn.execute("DELETE FROM chapters WHERE id!='demo-network-layer'")

    def test_four_subjects_and_five_paths_have_stable_unique_ids(self):
        data = self.client.get('/api/courses').json()
        self.assertEqual([s['id'] for s in data['subjects']], ['network', 'os', 'organization', 'data-structures'])
        self.assertEqual([c['lesson_count'] for c in data['courses']], [24, 12, 8, 8, 8])
        self.assertEqual([c['question_count'] for c in data['courses']], [72, 120, 32, 32, 32])
        ids = [i for c in data['courses'] for i in c['lesson_ids']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(reward_capacity(), 348)
        self.assertIsNone(course_id('unknown-not-a-course'))
        self.assertEqual(course_id('co-v1-cache'), 'organization')

    def test_new_lessons_open_validate_and_belong_only_to_their_course(self):
        for key in ('os', 'organization', 'data-structures'):
            catalog = self.client.get('/api/course/' + key).json()
            self.assertEqual(len(catalog['lessons']), 8)
            for lesson in catalog['lessons']:
                result = self.client.post(f'/api/course/{key}/lessons/{lesson["id"]}/open')
                self.assertEqual(result.status_code, 200, result.text)
                chapter = self.client.get('/api/chapters/' + lesson['id']).json()
                guide = StudyGuide.model_validate(chapter['guide'])
                self.assertGreaterEqual(len(guide.knowledge_points), 3)
                self.assertEqual(len(guide.quiz), 4)
                source_ids = {s['id'] for s in chapter['sources']}
                for p in guide.knowledge_points:
                    self.assertTrue(set(p.source_ids) <= source_ids)
        self.assertEqual(self.client.get('/api/course/missing').status_code, 404)
        self.assertEqual(self.client.post('/api/course/os/lessons/co-v1-cache/open').status_code, 404)
        self.assertEqual(self.client.post('/api/course/missing/lessons/os-v1-scheduling/open').status_code, 404)
        self.assertEqual(self.client.post('/api/course/network/lessons/os-v1-scheduling/open').status_code, 404)

    def test_notes_attempts_rewards_and_backup_roundtrip(self):
        lesson_id = 'os-v1-scheduling'
        self.client.post(f'/api/course/os/lessons/{lesson_id}/open')
        self.client.put(f'/api/chapters/{lesson_id}/notes', json={'notes': '等待时间口径', 'expected_revision': 0})
        q = core_courses.validated_lessons('os')[lesson_id]['guide']['quiz'][0]
        answer = self.client.post(f'/api/chapters/{lesson_id}/attempts', json={'question_index': 0, 'choice': q['answer']}).json()
        self.assertEqual(answer['reward_points'], 10)
        repeated = self.client.post(f'/api/chapters/{lesson_id}/attempts', json={'question_index': 0, 'choice': q['answer']}).json()
        self.assertEqual(repeated['reward_points'], 0)
        self.client.put(f'/api/chapters/{lesson_id}/progress', json={'completed': True})
        before = self.client.get('/api/chapters/' + lesson_id).json()
        self.client.post(f'/api/course/os/lessons/{lesson_id}/open')
        self.assertEqual(self.client.get('/api/chapters/' + lesson_id).json(), before)
        conflict = self.client.put(f'/api/chapters/{lesson_id}/notes', json={'notes': '旧版本', 'expected_revision': 0})
        self.assertEqual(conflict.status_code, 409)
        backup = self.client.get('/api/backups/export').content
        with db() as conn:
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
            conn.execute('DELETE FROM chapters WHERE id=?', (lesson_id,))
        result = self.client.post('/api/backups/import', files={'file': ('study.json', backup, 'application/json')})
        self.assertEqual(result.status_code, 200, result.text)
        restored = self.client.get('/api/chapters/' + lesson_id).json()
        self.assertEqual(restored['notes'], '等待时间口径')
        self.assertTrue(restored['completed'])
        self.assertEqual(len(restored['attempts']), 1)
        points = self.client.get('/api/rewards').json()['points']
        self.client.post('/api/backups/import', files={'file': ('study.json', backup, 'application/json')})
        self.assertEqual(self.client.get('/api/rewards').json()['points'], points)

    def test_reopen_does_not_replace_customized_import_or_edit(self):
        key = 'ds-v1-hashing'
        self.client.post(f'/api/course/data-structures/lessons/{key}/open')
        with db() as conn:
            conn.execute('UPDATE chapters SET title=?,notes=? WHERE id=?', ('保留个人标题', '保留个人笔记', key))
        self.client.post(f'/api/course/data-structures/lessons/{key}/open')
        result = self.client.get('/api/chapters/' + key).json()
        self.assertEqual((result['title'], result['notes']), ('保留个人标题', '保留个人笔记'))

    def test_daily_wrong_answer_returns_correct_new_course(self):
        key = 'co-v1-cache'
        self.client.post(f'/api/course/organization/lessons/{key}/open')
        self.client.post(f'/api/chapters/{key}/attempts', json={'question_index': 0, 'choice': 0})
        result = self.client.get('/api/rewards').json()['next_practice']
        self.assertEqual((result['chapter_id'], result['course_id']), (key, 'organization'))

    def test_export_contains_only_canonical_material_not_user_data(self):
        data = course_registry.public_bundle()
        target = Path(__file__).resolve().parents[1] / 'dist/academy/courses.json'
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), data)
        for c in data['courses']:
            for lesson in c['lessons']:
                self.assertNotIn('notes', lesson)
                self.assertNotIn('attempts', lesson)
        serialized = json.dumps(data)
        self.assertNotIn('api_key', serialized)

    def test_independent_reference_calculations_match_authored_examples(self):
        # Numeric truth is independently computed, not obtained from a model.
        self.assertEqual(5 * 4096 + 0x234, 0x5234)
        self.assertEqual(10**9 * 2 / (2 * 10**9), 1)
        self.assertAlmostEqual(1 / (.5 + .5 / 2), 4 / 3)
        self.assertEqual(int.from_bytes(bytes([0x78, 0x56, 0x34, 0x12]), 'little'), 0x12345678)
        self.assertEqual((5 + 10 - 1), 14)
        self.assertEqual(32 / 8 * 100 * 10**6, 400 * 10**6)
        self.assertEqual([k % 7 for k in [10,17,24]], [3,3,3])
        os_guide = core_courses.validated_lessons('os')['os-v1-paging']['guide']
        self.assertIn('0x5234', os_guide['worked_examples'][0]['answer'])
        co = core_courses.validated_lessons('organization')
        self.assertIn('25%', co['co-v1-cache']['guide']['worked_examples'][0]['answer'])


if __name__ == '__main__':
    unittest.main()
