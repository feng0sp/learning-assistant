"""Companion content, independently calculated answers and storage compatibility."""
import json
import hashlib
from copy import deepcopy
import math
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='zhizhang-micro-')
os.environ['STUDY_DATA_DIR'] = TEST_DIRECTORY.name
for key in ('STUDY_API_BASE_URL', 'STUDY_API_KEY', 'STUDY_MODEL'):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient
from backend.main import app
from backend.microclass import validated_lessons, VIDEO, LESSONS, refresh_saved_lessons, content_fingerprint
from backend.microclass_revisions import ORIGINAL_CONTENT, ORIGINAL_QUIZZES
from backend.models import StudyGuide
from backend.curriculum import reward_capacity
from backend.storage import db
from backend.ai import validate_guide


class MicroclassTests(unittest.TestCase):
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
            conn.execute("DELETE FROM chapters WHERE id != 'demo-network-layer'")
            conn.execute('DELETE FROM backup_imports')

    def open(self, slug):
        key = 'micro-v1-' + slug
        response = self.client.post('/api/course/microclass/lessons/' + key + '/open')
        self.assertEqual(response.status_code, 200, response.text)
        return self.client.get('/api/chapters/' + key + '/study').json()

    def answer(self, chapter, index):
        return self.client.post('/api/chapters/' + chapter['id'] + '/attempts', json={
            'question_index': index, 'choice': chapter['guide']['quiz'][index]['answer']})

    def upload(self, document):
        return self.client.post('/api/backups/import', files={'file': ('learning.json', json.dumps(document).encode(), 'application/json')})

    def test_complete_content_and_sources(self):
        catalog = self.client.get('/api/course/microclass').json()
        self.assertEqual(len(catalog['units']), 3)
        self.assertEqual(len(catalog['lessons']), 12)
        self.assertEqual([sum(i['questions'] for i in catalog['lessons'] if i['unit'] == n) for n in range(3)], [30, 30, 60])
        questions = set()
        reasoning = 0
        for item in validated_lessons().values():
            guide = validate_guide(item['guide'], item['sources'], True)
            self.assertEqual(len(guide['knowledge_points']), 6)
            self.assertEqual(len(guide['worked_examples']), 4)
            self.assertGreaterEqual(len(guide['analogies']), 1)
            self.assertEqual(len(guide['quiz']), 10)
            self.assertIn('尚未逐节核对视频字幕', guide['overview'])
            self.assertIn(VIDEO, item['sources'][0]['text'])
            reasoning += len(item['calculation_indices'])
            for index, question in enumerate(guide['quiz']):
                self.assertNotIn(question['question'], questions)
                questions.add(question['question'])
                self.assertTrue(question['question'].startswith('【' + ('基础' if index < 6 else '应用' if index < 9 else '综合') + '】'))
                lines = question['explanation'].splitlines()
                self.assertEqual(len(lines), 4)
                for option, line in enumerate(lines):
                    self.assertTrue(line.startswith(chr(65+option) + ('（正确）' if option == question['answer'] else '（错误）')))
        self.assertEqual(len(questions), 120)
        self.assertGreaterEqual(reasoning, 24)

    def test_numeric_answers_using_independent_calculations(self):
        def check(slug, index, expected):
            q = validated_lessons()['micro-v1-' + slug]['guide']['quiz'][index]
            self.assertEqual(q['options'][q['answer']], expected, q['question'])
        check('performance', 1, f'{1000*8/8e6*1000:g} ms')
        check('performance', 3, f'{600e3/2e8*1000:g} ms')
        check('performance', 6, f'{10e6*.02:g} bit')
        check('performance', 7, f'{(1500*8/10e6+1e6/2e8)*1000:g} ms')
        check('performance', 8, f'{(4+3-1)*1000/1e6*1000:g} ms')
        check('architecture', 6, f'{100+20+20+18} Byte')
        check('architecture', 7, f'{100/158*100:.2f}%')
        check('media', 6, f'{1/1e6*1e6:g} μs')
        check('media', 7, f'{8/10e6*1e6:g} μs')
        check('media', 8, f'{8/10*100:g}%')
        check('media', 9, f'{960*10/9600:g} s')
        check('encoding', 1, f'{math.log2(8):g} bit')
        check('encoding', 6, f'{2400*math.log2(16):g} bit/s')
        check('capacity', 0, f'{2*3000*math.log2(4):g} bit/s')
        check('capacity', 1, f'{10**(20/10):g}')
        check('capacity', 6, f'{4000*math.log2(1+15):g} bit/s')
        check('capacity', 7, f'{2*2000*math.log2(8):g} bit/s')
        check('capacity', 8, f'{3000*math.log2(1+3):g} bit/s')
        check('capacity', 9, f'{min(2*3000*math.log2(4),3000*math.log2(4)):g} bit/s')
        data, divisor = int('1101', 2), int('1011', 2)
        remainder = data << (divisor.bit_length()-1)
        while remainder.bit_length() >= divisor.bit_length():
            remainder ^= divisor << (remainder.bit_length()-divisor.bit_length())
        check('framing', 6, format(remainder, '03b'))
        check('framing', 7, format((data << 3) | remainder, '07b'))
        stuffed, ones = '', 0
        for bit in '01111110':
            stuffed += bit
            ones = ones+1 if bit == '1' else 0
            if ones == 5:
                stuffed += '0'
                ones = 0
        check('framing', 8, stuffed)
        check('reliable', 4, str(2**3-1))
        check('reliable', 5, str(2**(3-1)))
        check('reliable', 6, f'{1/(1+9)*100:g}%')
        check('ppp', 6, f'{8/4:g} Mbps')
        check('ppp', 9, f'{sum((a-b)*a for a,b in zip([1]*4,[1,-1,1,-1]))/4:+g}')
        check('access', 6, f'{2*25.6e-6*10e6:g} bit')
        check('access', 7, f'{64*8/10e6*1e6:g} μs')
        check('access', 8, f'{(2**3-1)*51.2:g} μs')
        check('vlan', 6, f'{3-1} 条')
        check('vlan', 8, str(2**12-2))

    def test_old_records_and_new_notes_survive_repeated_open(self):
        self.client.post('/api/course/network/lessons/net-v1-layers/open')
        self.client.put('/api/chapters/net-v1-layers/notes', json={'notes': '原有笔记', 'expected_revision': 0})
        before = self.client.get('/api/chapters/net-v1-layers').json()
        chapter = self.open('internet')
        self.client.put('/api/chapters/' + chapter['id'] + '/notes', json={'notes': '微课堂笔记', 'expected_revision': 0})
        self.answer(chapter, 9).raise_for_status()
        again = self.open('internet')
        self.assertEqual(again['notes'], '微课堂笔记')
        self.assertEqual(again['attempts'][0]['question_index'], 9)
        self.assertEqual(self.client.get('/api/chapters/net-v1-layers').json(), before)
        self.assertEqual(len(self.client.get('/api/course/network').json()['lessons']), 24)
        for suffix in ('print', 'export'):
            response = self.client.get('/api/chapters/' + chapter['id'] + '/' + suffix)
            self.assertEqual(response.status_code, 200)
            self.assertIn('微课堂笔记', response.text)

    def test_rewards_retries_and_legacy_badges(self):
        chapter = self.open('internet')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.answer(chapter, 0).json()['reward_points'], range(8)))
        self.assertEqual(sum(results), 10)
        self.assertEqual(sum(self.answer(chapter, i).json()['reward_points'] for i in range(1,10)), 110)
        self.assertEqual(self.answer(chapter, 9).json()['reward_points'], 0)
        growth = self.client.get('/api/rewards').json()
        self.assertEqual((growth['points'], growth['today']), (120, 10))
        self.assertFalse(any(b['earned'] for b in growth['badges']))

    def test_daily_practice_uses_microclass_route_for_wrong_answer(self):
        chapter = self.open('capacity')
        q = chapter['guide']['quiz'][8]
        self.client.post('/api/chapters/' + chapter['id'] + '/attempts', json={'question_index': 8, 'choice': (q['answer']+1)%4})
        target = self.client.get('/api/rewards').json()['next_practice']
        self.assertEqual((target['course_id'], target['chapter_id'], target['question_index']), ('microclass', chapter['id'], 8))

    def test_full_microclass_backup_over_old_96_event_limit(self):
        for lesson in validated_lessons().values():
            chapter = self.open(lesson['id'].removeprefix('micro-v1-'))
            for index in range(10):
                self.answer(chapter, index).raise_for_status()
        growth = self.client.get('/api/rewards').json()
        self.assertEqual((growth['points'], growth['level'], growth['title']), (1440, 5, '学有所成'))
        self.assertFalse(any(b['earned'] for b in growth['badges']))
        document = self.client.get('/api/backups/export').json()
        self.assertEqual(len(document['rewards']), 132)
        # Legacy 228 events plus 24 new core lessons × (4 answers + 1 milestone).
        self.assertEqual(reward_capacity(), 348)
        with db() as conn:
            conn.execute("DELETE FROM chapters WHERE id LIKE 'micro-v1-%'")
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
        restored = self.upload(document)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['points_added'], 1440)
        self.assertEqual(self.upload(document).json()['points_added'], 0)
        self.assertEqual(len(self.client.get('/api/chapters/micro-v1-vlan').json()['attempts']), 10)

    def test_unknown_paths_and_untrusted_modified_quiz(self):
        self.assertEqual(self.client.post('/api/course/microclass/lessons/net-v1-layers/open').status_code, 404)
        chapter = self.open('internet')
        self.assertEqual(self.client.post('/api/course/network/lessons/' + chapter['id'] + '/open').status_code, 404)
        altered = chapter['guide']
        altered['quiz'][0]['question'] = '用户改写的题目'
        with db() as conn:
            conn.execute('UPDATE chapters SET guide=? WHERE id=?', (json.dumps(altered), chapter['id']))
        self.assertEqual(self.answer(chapter, 0).json()['reward_points'], 0)

    def original(self, slug):
        item = deepcopy(next(item for item in LESSONS if item['id'] == 'micro-v1-' + slug))
        item['guide'] = StudyGuide.model_validate(item['guide']).model_dump()
        return item

    def seed_original(self, slug):
        self.open(slug)
        item = self.original(slug)
        with db() as conn:
            conn.execute('UPDATE chapters SET guide=?,sources=? WHERE id=?',
                         (json.dumps(item['guide']), json.dumps(item['sources']), item['id']))
        return item

    def test_released_quiz_identity_is_unchanged(self):
        for item in validated_lessons().values():
            quiz = json.dumps(item['guide']['quiz'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            self.assertEqual(hashlib.sha256(quiz.encode()).hexdigest(), ORIGINAL_QUIZZES[item['id']])
            old = self.original(item['id'].removeprefix('micro-v1-'))
            self.assertEqual(content_fingerprint(old['guide'], old['sources']), ORIGINAL_CONTENT[item['id']])
            self.assertEqual(sum(prompt.startswith('闭卷自查：') for prompt in item['guide']['objectives']), 2)

    def test_update_all_known_guides_keeps_every_user_field(self):
        before = {}
        for item in LESSONS:
            slug = item['id'].removeprefix('micro-v1-')
            self.seed_original(slug)
            key = item['id']
            chapter = self.client.get('/api/chapters/' + key).json()
            self.client.put('/api/chapters/' + key + '/notes', json={'notes': '旧版笔记 ' + key, 'expected_revision': 0}).raise_for_status()
            self.client.put('/api/chapters/' + key + '/progress', json={'completed': True}).raise_for_status()
            self.answer(chapter, 9).raise_for_status()
            with db() as conn:
                conn.execute('INSERT INTO messages(chapter_id,question,answer,created_at) VALUES (?,?,?,?)',
                             (key, '旧追问', '旧回答', '2026-10-12T00:00:00+00:00'))
            before[key] = self.client.get('/api/chapters/' + key).json()
        points = self.client.get('/api/rewards').json()['points']
        self.assertEqual(refresh_saved_lessons(), 12)
        self.assertEqual(refresh_saved_lessons(), 0)
        for key, old in before.items():
            new = self.client.get('/api/chapters/' + key).json()
            self.assertEqual(new['guide'], validated_lessons()[key]['guide'])
            self.assertEqual(new['sources'], validated_lessons()[key]['sources'])
            for field in old.keys() - {'guide', 'sources'}:
                self.assertEqual(new[field], old[field], (key, field))
            self.assertEqual(self.answer(new, 9).json()['reward_points'], 0)
        self.assertEqual(self.client.get('/api/rewards').json()['points'], points)

    def test_open_and_startup_refresh_old_content(self):
        self.seed_original('capacity')
        self.assertEqual(len(self.open('capacity')['guide']['worked_examples']), 4)
        self.seed_original('framing')
        with TestClient(app) as restarted:
            restored = restarted.get('/api/chapters/micro-v1-framing').json()
            self.assertEqual(len(restored['guide']['knowledge_points']), 6)
        self.assertEqual(refresh_saved_lessons('not-a-lesson'), 0)

    def test_edited_import_is_never_replaced(self):
        original = self.seed_original('internet')
        altered = deepcopy(original)
        altered['guide']['knowledge_points'][0]['explanation'] = '用户自行扩充的资料'
        with db() as conn:
            conn.execute('UPDATE chapters SET guide=? WHERE id=?', (json.dumps(altered['guide']), original['id']))
        self.assertEqual(refresh_saved_lessons(), 0)
        self.assertEqual(self.open('internet')['guide'], altered['guide'])
        original = self.seed_original('media')
        original['sources'][0]['text'] += '\n用户追加的来源'
        with db() as conn:
            conn.execute('UPDATE chapters SET sources=? WHERE id=?', (json.dumps(original['sources']), original['id']))
        self.assertEqual(refresh_saved_lessons(), 0)
        self.assertEqual(self.open('media')['sources'], original['sources'])

    def test_old_backup_upgrades_once_without_losing_rewards(self):
        old = self.seed_original('ppp')
        chapter = self.client.get('/api/chapters/' + old['id']).json()
        self.client.put('/api/chapters/' + old['id'] + '/notes', json={'notes': '转义复习笔记', 'expected_revision': 0}).raise_for_status()
        self.answer(chapter, 8).raise_for_status()
        document = self.client.get('/api/backups/export').json()
        with db() as conn:
            conn.execute('DELETE FROM chapters WHERE id=?', (old['id'],))
            conn.execute('DELETE FROM learning_rewards')
            conn.execute('DELETE FROM learning_days')
        result = self.upload(document)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['points_added'], 10)
        restored = self.client.get('/api/chapters/' + old['id']).json()
        self.assertEqual(restored['notes'], '转义复习笔记')
        self.assertEqual(len(restored['guide']['worked_examples']), 4)
        self.assertEqual(restored['attempts'], chapter['attempts'] + [{'question_index': 8, 'choice': chapter['guide']['quiz'][8]['answer'], 'correct': 1}])
        retry = self.upload(document).json()
        self.assertEqual(retry['imported'], 0)
        self.assertEqual(retry['copies'], 0)
        self.assertEqual(retry['points_added'], 0)
        self.assertEqual(self.answer(restored, 8).json()['reward_points'], 0)

    def test_supplement_calculations_and_processes(self):
        def answer(slug, index, expected):
            example = validated_lessons()['micro-v1-' + slug]['guide']['worked_examples'][index + 2]
            self.assertIn(expected, example['answer'], example['question'])
        # Independent schedule simulation rather than the equal-rate shortcut.
        link_free = [0.0, 0.0]
        for packet in range(2):
            arrived = 0.0
            for link, rate in enumerate([1e6, .5e6]):
                arrived = max(arrived, link_free[link]) + 1000 / rate
                link_free[link] = arrived
        answer('performance', 0, f'{arrived*1000:g} ms')
        answer('performance', 1, f'{10*1000/1040:.3f} Mbps')
        answer('architecture', 0, f'{200+8+20+18} Byte')
        answer('media', 0, f'{11000/(1+7+1+2)*7:g} bit/s')
        answer('media', 1, f'{10e6*8/100e6:g} s')
        answer('encoding', 0, f'{2000*math.log2(8)*4/5:g} bit/s')
        answer('encoding', 1, '｜'.join('低高' if bit == '1' else '高低' for bit in '1010'))
        required = 2**(12000/3000)-1
        answer('capacity', 0, f'{10*math.log10(required):.2f} dB')
        answer('capacity', 1, f'M≥{2**(16000/(2*2000)):g}')
        answer('capacity', 1, f'{2000*math.log2(16):g} bit/s')
        def remainder(bits, generator):
            work = [int(bit) for bit in bits]
            divisor = [int(bit) for bit in generator]
            for start in range(len(work)-len(divisor)+1):
                if work[start]:
                    for offset, bit in enumerate(divisor):
                        work[start+offset] ^= bit
            return ''.join(map(str, work[-(len(divisor)-1):]))
        crc = remainder('1011' + '000', '1101')
        answer('framing', 0, '余数 ' + crc)
        code = '1011' + crc
        self.assertEqual(remainder(code, '1101'), '000')
        received = ''.join(str(int(a)^int(b)) for a,b in zip(code, '0001101'))
        self.assertEqual(remainder(received, '1101'), '000')
        answer('framing', 1, received)
        old_window = set(range(5))
        new_window = {(5+offset)%8 for offset in range(5)}
        answer('reliable', 1, '{'+','.join(map(str, sorted(old_window & new_window)))+'}')
        encoded = []
        for byte in (0x41, 0x7E, 0x7D, 0x42):
            encoded.extend([0x7D, byte^0x20] if byte in (0x7D, 0x7E) else [byte])
        answer('ppp', 0, f'{len(encoded)} Byte')
        answer('ppp', 0, f'增加 {len(encoded)-4} Byte')
        answer('ppp', 1, f'{8/(40/1e6)/1000:g} kbit/s')
        answer('access', 0, f'{2*9+100+34+3*9} μs')
        table, outputs = {}, []
        for source, destination, ingress in [('A','B',1),('B','A',2),('A','B',1)]:
            table[source] = ingress
            outputs.append({table[destination]} if destination in table else {1,2,3}-{ingress})
        self.assertEqual(outputs, [{2,3}, {1}, {2}])
        answer('addressing', 1, '、'.join('{'+','.join(map(str, sorted(ports)))+'}' for ports in outputs))
        root = min([(32768,0x0a,'A'), (28672,0xff,'B'), (28672,0x10,'C')])[2]
        answer('vlan', 0, root + ' 为根桥')
        frame_bytes = 1500+14+4+4
        answer('vlan', 1, f'{frame_bytes} Byte')
        answer('vlan', 1, f'{frame_bytes*8/100e6*1e6:.2f} μs')


if __name__ == '__main__':
    unittest.main(verbosity=2)
