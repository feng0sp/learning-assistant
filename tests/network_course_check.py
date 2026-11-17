"""Offline curriculum integration and independent numerical checks."""
import os
import tempfile
import unittest

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-course-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient
from backend.main import app
from backend.ai import validate_guide
from backend.network_course import validated_lessons


class NetworkCourseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        TEST_DIRECTORY.cleanup()

    def test_catalog_and_all_lessons(self):
        catalog = self.client.get('/api/course/network').json()
        self.assertEqual(len(catalog['lessons']), 24)
        self.assertEqual({i['unit'] for i in catalog['lessons']}, set(range(8)))
        self.assertEqual(sum(i['questions'] for i in catalog['lessons']), 72)
        for item in catalog['lessons']:
            result = self.client.post(f"/api/course/network/lessons/{item['id']}/open")
            self.assertEqual(result.status_code, 200, result.text)
            chapter = self.client.get('/api/chapters/' + item['id']).json()
            validate_guide(chapter['guide'], chapter['sources'], True)
            self.assertEqual(len(chapter['guide']['quiz']), 3)
            self.assertIn('非教材原文', chapter['sources'][0]['label'])
            self.assertEqual(self.client.get('/api/chapters/' + item['id'] + '/print').status_code, 200)
        self.assertFalse(self.client.get('/api/settings').json()['configured'])

    def test_progress_notes_and_repeated_open(self):
        chapter_id = 'net-v1-ipv4'
        endpoint = '/api/course/network/lessons/' + chapter_id + '/open'
        self.client.post(endpoint).raise_for_status()
        self.client.put('/api/chapters/' + chapter_id + '/notes', json={'notes': '保留我的子网计算', 'expected_revision': 0}).raise_for_status()
        self.client.put('/api/chapters/' + chapter_id + '/progress', json={'completed': True}).raise_for_status()
        self.client.post('/api/chapters/' + chapter_id + '/attempts', json={'question_index': 0, 'choice': 1}).raise_for_status()
        self.client.post(endpoint).raise_for_status()
        chapter = self.client.get('/api/chapters/' + chapter_id).json()
        self.assertEqual(chapter['notes'], '保留我的子网计算')
        self.assertEqual(len(chapter['attempts']), 1)
        progress = next(i for i in self.client.get('/api/course/network').json()['lessons'] if i['id'] == chapter_id)
        self.assertEqual((progress['completed'], progress['attempted'], progress['correct']), (True, 1, 1))

    def test_exams_and_backup(self):
        covered = set()
        for paper in (1, 2):
            result = self.client.post(f'/api/course/network/exams/{paper}/open').json()
            chapter = self.client.get('/api/chapters/' + result['chapter_id']).json()
            validate_guide(chapter['guide'], chapter['sources'], True)
            self.assertEqual(len(chapter['guide']['quiz']), 12)
            covered.update(q['question'].split('】')[0][1:] for q in chapter['guide']['quiz'])
        self.assertEqual(covered, {item['title'] for item in validated_lessons().values()})
        backup = self.client.get('/api/backups/export')
        self.assertEqual(backup.status_code, 200, backup.text[:200])
        preview = self.client.post('/api/backups/preview', files={'file': ('course.json', backup.content, 'application/json')})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['new'], 0)
        self.assertEqual(preview.json()['copies'], 0)

    def test_subnet_boundaries(self):
        cases = [('192.168.1.70/26', '192.168.1.64/26', '62'), ('10.0.0.0/31', '10.0.0.0/31', '2'), ('10.0.0.1/32', '10.0.0.1/32', '1'), ('255.255.255.255/0', '0.0.0.0/0', '4294967294')]
        for address, title, count in cases:
            response = self.client.post('/api/course/network/labs/subnet', json={'address': address})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['title'], title)
            self.assertTrue(any('数量：' + count in line for line in response.json()['steps']))
        for address in ('10.0.0.1', '10.0.0.1/33', '256.1.1.1/24', '::1/128'):
            self.assertEqual(self.client.post('/api/course/network/labs/subnet', json={'address': address}).status_code, 422)

    def test_delay_units_and_pipeline(self):
        base = {'bytes': 1500, 'mbps': 10, 'distance_km': 1000, 'speed': 200000000, 'hops': 1, 'packets': 1}
        self.assertAlmostEqual(self.client.post('/api/course/network/labs/delay', json=base).json()['total_ms'], 6.2)
        pipeline = dict(base, bytes=125, mbps=1, distance_km=0, hops=3, packets=4)
        self.assertAlmostEqual(self.client.post('/api/course/network/labs/delay', json=pipeline).json()['total_ms'], 6)
        for patch in ({'speed': 0}, {'mbps': -1}, {'hops': 1.5}, {'packets': 0}, {'mbps': 1e-100}):
            self.assertEqual(self.client.post('/api/course/network/labs/delay', json=dict(base, **patch)).status_code, 422)

    def test_crc_codeword_divisibility_and_limits(self):
        for data, polynomial in [('1101', '1011'), ('0', '11'), ('11110000', '10011'), ('1'*256, '1'+'0'*30+'1')]:
            response = self.client.post('/api/course/network/labs/crc', json={'data': data, 'generator': polynomial})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            dividend, divisor = int(result['codeword'], 2), int(polynomial, 2)
            while dividend and dividend.bit_length() >= divisor.bit_length():
                dividend ^= divisor << (dividend.bit_length() - divisor.bit_length())
            self.assertEqual(dividend, 0)
            self.assertEqual(len(result['remainder']), len(polynomial)-1)
            if data == '1101':
                self.assertEqual(result['remainder'], '001')
        for payload in ({'data': '102', 'generator': '1011'}, {'data': '1', 'generator': '1'}, {'data': '1'*257, 'generator': '11'}, {'data': '1', 'generator': '10'}):
            self.assertEqual(self.client.post('/api/course/network/labs/crc', json=payload).status_code, 422)

    def test_unknown_course_items(self):
        self.assertEqual(self.client.post('/api/course/network/lessons/missing/open').status_code, 404)
        self.assertEqual(self.client.post('/api/course/network/exams/3/open').status_code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
