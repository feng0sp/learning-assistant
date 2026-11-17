"""Reward persistence, transaction deduplication and practice-day boundaries."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-rewards-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient
from backend.main import app
from backend.storage import db, initialize


class RewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()
        cls.client.post('/api/course/network/lessons/net-v1-layers/open').raise_for_status()
        cls.quiz = cls.client.get('/api/chapters/net-v1-layers').json()['guide']['quiz']

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        TEST_DIRECTORY.cleanup()

    def setUp(self):
        with db() as conn:
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
            conn.execute('DELETE FROM attempts')

    def answer(self, index, correct=True):
        response = self.client.post('/api/chapters/net-v1-layers/attempts', json={'question_index': index, 'choice': self.quiz[index]['answer'] if correct else (self.quiz[index]['answer'] + 1) % 4})
        response.raise_for_status()
        return response.json()['reward_points']

    def test_first_correct_and_bonus_only_once(self):
        self.assertEqual(self.answer(0, False), 0)
        self.assertEqual(self.answer(0), 10)
        self.assertEqual(self.answer(0), 0)
        self.assertEqual(self.answer(1), 10)
        self.assertEqual(self.answer(2), 30)
        self.assertEqual(self.answer(2, False), 0)
        self.assertEqual(self.answer(2), 0)
        result = self.client.get('/api/rewards').json()
        self.assertEqual((result['points'], result['today'], result['lessons']), (50, 3, 1))
        self.assertEqual(sum(b['earned'] for b in result['badges']), 2)

    def test_concurrent_retries_do_not_duplicate_points(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            points = list(pool.map(lambda _: self.answer(0), range(12)))
        self.assertEqual(sum(points), 10)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 10)

    def test_manual_completion_demo_and_exam_do_not_reward(self):
        for value in (True, False, True):
            self.client.put('/api/chapters/net-v1-layers/progress', json={'completed': value}).raise_for_status()
        self.client.post('/api/chapters/demo-network-layer/attempts', json={'question_index': 0, 'choice': 0}).raise_for_status()
        exam_id = self.client.post('/api/course/network/exams/1/open').json()['chapter_id']
        answer = self.client.get('/api/chapters/' + exam_id).json()['guide']['quiz'][0]['answer']
        self.client.post('/api/chapters/' + exam_id + '/attempts', json={'question_index': 0, 'choice': answer}).raise_for_status()
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 0)

    def test_reinitialize_keeps_rewards(self):
        self.answer(0)
        initialize()
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 10)
        self.assertEqual(self.answer(0), 0)

    def test_next_day_review_counts_without_extra_points(self):
        self.answer(0)
        tomorrow = datetime.now().astimezone() + timedelta(days=1)
        with patch('backend.rewards.datetime') as clock:
            clock.now.return_value = tomorrow
            self.assertEqual(self.client.get('/api/rewards').json()['today'], 0)
            self.assertEqual(self.answer(0), 0)
            self.assertEqual(self.answer(0), 0)
            result = self.client.get('/api/rewards').json()
            self.assertEqual((result['points'], result['today']), (10, 1))

    def test_invalid_attempt_cannot_create_reward(self):
        response = self.client.post('/api/chapters/net-v1-layers/attempts', json={'question_index': 99, 'choice': 0})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 0)

    def test_daily_recommendation_prioritizes_wrong_course_question(self):
        chapter_id = 'net-v1-ipv6'
        self.client.post('/api/course/network/lessons/' + chapter_id + '/open').raise_for_status()
        answer = self.client.get('/api/chapters/' + chapter_id).json()['guide']['quiz'][2]['answer']
        self.client.post('/api/chapters/' + chapter_id + '/attempts', json={'question_index': 2, 'choice': (answer + 1) % 4}).raise_for_status()
        target = self.client.get('/api/rewards').json()['next_practice']
        self.assertEqual((target['chapter_id'], target['question_index']), (chapter_id, 2))
        self.assertIn('错题', target['reason'])

    def test_daily_recommendation_moves_past_correct_questions(self):
        self.answer(0)
        target = self.client.get('/api/rewards').json()['next_practice']
        self.assertEqual((target['chapter_id'], target['question_index']), ('net-v1-layers', 1))
        self.answer(1)
        self.answer(2)
        target = self.client.get('/api/rewards').json()['next_practice']
        self.assertEqual((target['chapter_id'], target['question_index']), ('net-v1-delay', 0))

    def test_full_curriculum_unlocks_all_levels_without_repeat_rewards(self):
        total = 0
        for lesson in self.client.get('/api/course/network').json()['lessons']:
            chapter_id = lesson['id']
            self.client.post('/api/course/network/lessons/' + chapter_id + '/open').raise_for_status()
            quiz = self.client.get('/api/chapters/' + chapter_id).json()['guide']['quiz']
            for index, question in enumerate(quiz):
                response = self.client.post('/api/chapters/' + chapter_id + '/attempts', json={'question_index': index, 'choice': question['answer']})
                total += response.json()['reward_points']
        self.assertEqual(total, 1200)
        result = self.client.get('/api/rewards').json()
        self.assertEqual((result['points'], result['level'], result['lessons'], result['today']), (1200, 5, 24, 72))
        self.assertTrue(all(item['earned'] for item in result['badges']))
        self.assertIsNone(result['next_level'])
        self.assertEqual(result['next_practice']['course_id'], 'microclass')
        self.assertEqual(self.answer(0), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
