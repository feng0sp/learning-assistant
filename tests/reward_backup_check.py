"""Versioned growth backups: migration, deduplication and atomic recovery."""
import copy
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-reward-backup-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient
from backend.main import app
from backend.storage import db


class RewardBackupTests(unittest.TestCase):
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
            conn.execute('DELETE FROM chapters WHERE id != ?', ('demo-network-layer',))
            conn.execute('DELETE FROM backup_imports')
        self.chapter_id = 'net-v1-layers'
        self.client.post('/api/course/network/lessons/' + self.chapter_id + '/open').raise_for_status()
        self.quiz = self.client.get('/api/chapters/' + self.chapter_id).json()['guide']['quiz']

    def answer(self, index):
        return self.client.post('/api/chapters/' + self.chapter_id + '/attempts', json={'question_index': index, 'choice': self.quiz[index]['answer']})

    def earned_document(self):
        for index in range(3):
            self.answer(index).raise_for_status()
        return self.client.get('/api/backups/export').json()

    def upload(self, document, preview=False):
        return self.client.post('/api/backups/' + ('preview' if preview else 'import'), files={'file': ('learning.json', json.dumps(document).encode('utf-8'), 'application/json')})

    def test_export_includes_versioned_growth_records(self):
        document = self.earned_document()
        self.assertEqual(document['format_version'], 2)
        self.assertEqual(len(document['rewards']), 4)
        self.assertEqual(sum(r['points'] for r in document['rewards']), 50)
        self.assertEqual(len(document['practice_days']), 3)
        self.assertNotIn('settings', document)

    def test_restore_to_empty_database_and_retry(self):
        document = self.earned_document()
        with db() as conn:
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
            conn.execute('DELETE FROM chapters WHERE id=?', (self.chapter_id,))
        preview = self.upload(document, True).json()
        self.assertEqual((preview['rewards_new'], preview['points_new'], preview['practice_days_new']), (4, 50, 3))
        result = self.upload(document)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 50)
        self.assertEqual(self.upload(document).json()['points_added'], 0)
        self.assertEqual(self.answer(0).json()['reward_points'], 0)
        self.assertEqual(self.client.get('/api/rewards').json()['today'], 3)

    def test_rewards_only_restore_when_chapters_already_exist(self):
        document = self.earned_document()
        with db() as conn:
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
        preview = self.upload(document, True).json()
        self.assertEqual((preview['new'], preview['copies']), (0, 0))
        self.assertEqual(preview['points_new'], 50)
        self.assertEqual(self.upload(document).json()['points_added'], 50)
        self.assertEqual(self.upload(document, True).json()['rewards_new'], 0)

    def test_simultaneous_restores_and_answers_share_one_reward_ledger(self):
        document = self.earned_document()
        with db() as conn:
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = [pool.submit(self.upload, document) for _ in range(8)]
            jobs += [pool.submit(self.answer, i % 3) for i in range(12)]
            responses = [job.result() for job in jobs]
        added = 0
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
            value = response.json()
            added += value.get('points_added', value.get('reward_points', 0))
        self.assertEqual(added, 50)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 50)
        self.assertEqual(self.client.get('/api/rewards').json()['today'], 3)
        restored = self.client.get('/api/backups/export').json()
        self.assertEqual(len(restored['rewards']), 4)
        self.assertEqual(len(restored['practice_days']), 3)

    def test_version_one_keeps_existing_growth(self):
        document = self.earned_document()
        document['format_version'] = 1
        document.pop('rewards')
        document.pop('practice_days')
        result = self.upload(document)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['points_added'], 0)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 50)

    def test_invalid_growth_rejected_before_import(self):
        document = self.earned_document()
        invalid = []
        for key, value in [('points', 1000), ('kind', 'anything'), ('event_key', 'answer:unknown:0'), ('earned_at', 'yesterday')]:
            item = copy.deepcopy(document)
            item['rewards'][0][key] = value
            invalid.append(item)
        item = copy.deepcopy(document)
        item['rewards'].append(item['rewards'][0])
        invalid.append(item)
        item = copy.deepcopy(document)
        item['practice_days'][0]['day'] = '2026-02-30'
        invalid.append(item)
        item = copy.deepcopy(document)
        item.pop('rewards')
        invalid.append(item)
        for item in invalid:
            response = self.upload(item)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 50)

    def test_failure_rolls_back_chapters_and_growth_together(self):
        document = self.earned_document()
        with db() as conn:
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
            conn.execute('DELETE FROM chapters WHERE id=?', (self.chapter_id,))
            conn.execute("CREATE TRIGGER reject_reward_restore BEFORE INSERT ON learning_rewards BEGIN SELECT RAISE(ABORT, 'test recovery rollback'); END")
        try:
            response = self.upload(document)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.client.get('/api/chapters/' + self.chapter_id).status_code, 404)
            self.assertEqual(self.client.get('/api/rewards').json()['points'], 0)
        finally:
            with db() as conn:
                conn.execute('DROP TRIGGER reject_reward_restore')
        self.assertEqual(self.upload(document).status_code, 200)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], 50)


if __name__ == '__main__':
    unittest.main(verbosity=2)
