"""Bounded study responses and stable older-message cursors."""
import os
import tempfile
import unittest

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-message-page-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient
from backend.main import app
from backend.storage import db, now


class MessagePageTests(unittest.TestCase):
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
            conn.execute('DELETE FROM messages')
            conn.executemany('INSERT INTO messages(chapter_id,question,answer,created_at) VALUES (?,?,?,?)',
                [('demo-network-layer', f'问题 {i:03d}', '保存的详细回答。' * 150, now()) for i in range(100)])

    def test_study_payload_is_bounded_and_full_detail_remains_available(self):
        full = self.client.get('/api/chapters/demo-network-layer')
        study = self.client.get('/api/chapters/demo-network-layer/study')
        self.assertEqual(study.status_code, 200)
        self.assertEqual(len(full.json()['messages']), 100)
        self.assertEqual(len(study.json()['messages']), 30)
        self.assertEqual(study.json()['messages'][0]['question'], '问题 070')
        self.assertTrue(study.json()['messages_has_more'])
        self.assertLess(len(study.content), len(full.content) * .4)
        self.assertEqual(study.json()['guide'], full.json()['guide'])

    def test_cursor_reads_each_old_message_once_during_new_insert(self):
        first = self.client.get('/api/chapters/demo-network-layer/study').json()
        ids = [item['id'] for item in first['messages']]
        cursor = first['messages_cursor']
        with db() as conn:
            conn.execute('INSERT INTO messages(chapter_id,question,answer,created_at) VALUES (?,?,?,?)', ('demo-network-layer', '翻页期间的新回答', '不影响旧记录游标', now()))
        while cursor is not None:
            response = self.client.get('/api/chapters/demo-network-layer/messages', params={'before': cursor})
            self.assertEqual(response.status_code, 200, response.text)
            page = response.json()
            self.assertLessEqual(len(page['messages']), 30)
            self.assertTrue(all(item['id'] < cursor for item in page['messages']))
            ids = [item['id'] for item in page['messages']] + ids
            cursor = page['messages_cursor']
        self.assertEqual(len(ids), 100)
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(len(self.client.get('/api/chapters/demo-network-layer').json()['messages']), 101)
        backup = self.client.get('/api/backups/export').json()
        self.assertEqual(len(backup['chapters'][0]['messages']), 101)

    def test_empty_short_and_missing_chapters(self):
        with db() as conn:
            conn.execute('DELETE FROM messages')
        empty = self.client.get('/api/chapters/demo-network-layer/study').json()
        self.assertEqual(empty['messages'], [])
        self.assertFalse(empty['messages_has_more'])
        self.assertIsNone(empty['messages_cursor'])
        self.assertEqual(self.client.get('/api/chapters/missing/study').status_code, 404)
        self.assertEqual(self.client.get('/api/chapters/missing/messages?before=10').status_code, 404)

    def test_invalid_cursor_and_limits(self):
        for query in ('before=0', 'before=-1', 'before=abc', 'before=99999999999999999999999999', 'before=10&limit=0', 'before=10&limit=101'):
            self.assertEqual(self.client.get('/api/chapters/demo-network-layer/messages?' + query).status_code, 422)


if __name__ == '__main__':
    unittest.main(verbosity=2)
