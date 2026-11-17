import io
import json
import os
import tempfile
import unittest
from pathlib import Path

# Isolation is established before importing the application. Real user data and keys are never used.
TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix="zhizhang-tests-")
os.environ["STUDY_DATA_DIR"] = TEST_DIRECTORY.name
for key in ("STUDY_API_BASE_URL", "STUDY_API_KEY", "STUDY_MODEL"):
    os.environ.pop(key, None)

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.main import app
from backend.storage import db, dumps, now
from tests.fake_provider import FakeProvider


class StudyAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = FakeProvider().start()
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.provider.close()
        TEST_DIRECTORY.cleanup()

    def setUp(self):
        self.provider.mode = "normal"
        self.provider.calls.clear()
        with db() as conn:
            conn.execute("DELETE FROM jobs")
        result = self.client.put("/api/settings", json={"base_url": self.provider.base_url, "model": "test-model", "api_key": "test-only-key"})
        self.assertEqual(result.status_code, 200)

    def generate(self, **overrides):
        value = {"course": "计算机网络", "chapter": "网络层", "mode": "topic", **overrides}
        result = self.client.post("/api/chapters/generate", json=value)
        self.assertEqual(result.status_code, 202, result.text)
        job = self.client.get("/api/jobs/" + result.json()["job_id"]).json()
        return job

    def test_offline_demo_and_download(self):
        result = self.client.get("/api/chapters/demo-network-layer")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["mode"], "demo")
        exported = self.client.get("/api/chapters/demo-network-layer/export")
        self.assertIn("非教材原文", exported.text)
        self.assertIn("自测答案与解析", exported.text)
        self.assertTrue(exported.headers["content-disposition"].startswith("attachment"))

    def test_title_mode_never_attaches_fake_textbook_sources(self):
        job = self.generate(textbook="测试教材第 2 版", text="不应传给模型的隐藏草稿")
        self.assertEqual(job["status"], "completed", job)
        chapter = self.client.get("/api/chapters/" + job["chapter_id"]).json()
        self.assertEqual(chapter["mode"], "topic")
        self.assertEqual(chapter["sources"], [])
        self.assertTrue(all(not p["source_ids"] for p in chapter["guide"]["knowledge_points"]))
        self.assertNotIn("隐藏草稿", json.dumps(self.provider.calls, ensure_ascii=False))

    def test_upload_select_pages_and_generate_grounded_chapter(self):
        text = "网络层负责将数据分组从源主机送到目的主机。IP 提供尽力而为的交付。" * 3
        uploaded = self.client.post("/api/materials", files={"file": ("章节笔记.txt", text.encode("utf-8"), "text/plain")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        material = uploaded.json()
        self.assertEqual(material["preview"], text)
        self.assertEqual(self.client.get(f"/api/materials/{material['id']}/pages/1").json()["text"], text)
        job = self.generate(mode="material", material_id=material["id"], page_start=1, page_end=1)
        self.assertEqual(job["status"], "completed", job)
        chapter = self.client.get("/api/chapters/" + job["chapter_id"]).json()
        self.assertEqual(chapter["sources"][0]["id"], "p1-s1")
        self.assertEqual(chapter["sources"][0]["text"], text)
        self.assertTrue(all("p1-s1" in p["source_ids"] for p in chapter["guide"]["knowledge_points"]))

    def test_wrong_pages_and_missing_material_fail_before_model_call(self):
        text = "网络层根据目的地址把分组转发到下一跳，IP 不保证可靠交付。" * 3
        material = self.client.post("/api/materials", files={"file": ("note.md", text.encode(), "text/plain")}).json()
        for extra in ({"material_id": material["id"], "page_start": 1, "page_end": 2}, {"material_id": "missing"}):
            result = self.client.post("/api/chapters/generate", json={"course": "网络", "chapter": "网络层", "mode": "material", **extra})
            self.assertIn(result.status_code, (404, 422))
        self.assertEqual(self.provider.calls, [])

    def test_scanned_or_blank_pdf_is_not_silently_summarized(self):
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        buffer = io.BytesIO()
        writer.write(buffer)
        result = self.client.post("/api/materials", files={"file": ("scan.pdf", buffer.getvalue(), "application/pdf")})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["warnings"])
        generated = self.client.post("/api/chapters/generate", json={"course": "网络", "chapter": "网络层", "mode": "material", "material_id": result.json()["id"]})
        self.assertEqual(generated.status_code, 422)
        self.assertIn("无法提取文字", generated.text)
        self.assertEqual(self.provider.calls, [])

    def test_long_chapter_reads_all_chunks_before_synthesis(self):
        first = "前半部分：IP 不保证可靠交付。\n" * 650
        last = "后半部分：路由器使用最长前缀匹配。\n" * 650
        job = self.generate(mode="material", text=first + last)
        self.assertEqual(job["status"], "completed", job)
        outline_calls = [call for call in self.provider.calls if "提取教材知识结构" in call["payload"]["messages"][0]["content"]]
        self.assertGreaterEqual(len(outline_calls), 2)
        all_input = "".join(call["payload"]["messages"][1]["content"] for call in outline_calls)
        self.assertIn("前半部分", all_input)
        self.assertIn("后半部分", all_input)
        self.assertEqual(all_input.count("最长前缀匹配"), 650)

    def test_invalid_model_results_are_repaired_once_then_not_saved(self):
        before = len(self.client.get("/api/chapters").json())
        for mode in ("bad_json", "bad_citations", "blank_steps"):
            self.provider.mode = mode
            self.provider.calls.clear()
            job = self.generate(mode="material", text="网络层通过路由器跨网络转发数据，IP 采用尽力而为的交付机制，不保证可靠交付。")
            self.assertEqual(job["status"], "failed")
            self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(len(self.client.get("/api/chapters").json()), before)

    def test_blank_teaching_steps_must_be_repaired_before_saving(self):
        self.provider.mode = "repair_blank_steps"
        job = self.generate()
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(len(self.provider.calls), 2)
        correction = self.provider.calls[1]["payload"]["messages"][-1]["content"]
        self.assertIn("worked_examples.0.steps.0", correction)
        chapter = self.client.get("/api/chapters/" + job["chapter_id"]).json()
        self.assertTrue(all(step.strip() for example in chapter["guide"]["worked_examples"] for step in example["steps"]))

    def test_credentials_redacted_and_not_reused_for_new_endpoint(self):
        settings = self.client.get("/api/settings")
        self.assertTrue(settings.json()["has_key"])
        self.assertNotIn("test-only-key", settings.text)
        ping = self.client.post("/api/settings/test")
        self.assertEqual(ping.status_code, 200)
        self.assertEqual(self.provider.calls[-1]["authorization"], "Bearer test-only-key")
        self.client.put("/api/settings", json={"base_url": self.provider.base_url + "/other", "model": "other"})
        self.assertFalse(self.client.get("/api/settings").json()["has_key"])

    def test_provider_failures_have_actionable_safe_errors(self):
        for mode, expected in (("unauthorized", "密钥"), ("rate_limited", "限流"), ("truncated", "长度限制")):
            self.provider.mode = mode
            result = self.client.post("/api/settings/test")
            self.assertEqual(result.status_code, 422)
            self.assertIn(expected, result.json()["detail"])
            self.assertNotIn("test-only-key", result.text)

    def test_notes_attempts_progress_and_followups_survive_readback(self):
        job = self.generate()
        url = "/api/chapters/" + job["chapter_id"]
        self.assertEqual(self.client.put(url + "/notes", json={"notes": "转发执行下一跳，路由选择规划路径。"}).status_code, 200)
        attempt = self.client.post(url + "/attempts", json={"question_index": 0, "choice": 1}).json()
        self.assertFalse(attempt["correct"])
        self.client.post(url + "/attempts", json={"question_index": 0, "choice": 0})
        self.client.put(url + "/progress", json={"completed": True})
        answer = self.client.post(url + "/questions", json={"question": "请解释转发与路由选择的不同。"})
        self.assertEqual(answer.status_code, 200, answer.text)
        with TestClient(app) as reopened:
            chapter = reopened.get(url).json()
        self.assertTrue(chapter["completed"])
        self.assertIn("下一跳", chapter["notes"])
        self.assertEqual(len(chapter["attempts"]), 1)
        self.assertTrue(chapter["attempts"][0]["correct"])
        self.assertEqual(len(chapter["messages"]), 1)

    def test_review_uses_latest_wrong_answers_and_does_not_reveal_solutions(self):
        first = self.generate(chapter="需要复习的网络层")['chapter_id']
        second = self.generate(course="另一门课程", chapter="跨课程错题")['chapter_id']
        self.client.post(f'/api/chapters/{first}/attempts', json={'question_index': 0, 'choice': 1})
        self.client.post(f'/api/chapters/{first}/attempts', json={'question_index': 1, 'choice': 2})
        self.client.post(f'/api/chapters/{second}/attempts', json={'question_index': 2, 'choice': 0})
        rows = self.client.get('/api/review').json()
        own = {(row['chapter_id'], row['question_index']): row for row in rows if row['chapter_id'] in {first, second}}
        self.assertEqual(set(own), {(first, 0), (second, 2)})
        self.assertEqual(own[first, 0]['previous_choice'], 1)
        self.assertEqual(own[second, 2]['course'], '另一门课程')
        for row in own.values():
            self.assertEqual(len(row['options']), 4)
            self.assertNotIn('answer', row)
            self.assertNotIn('explanation', row)
        self.client.post(f'/api/chapters/{first}/attempts', json={'question_index': 0, 'choice': 3})
        updated = next(row for row in self.client.get('/api/review').json() if row['chapter_id'] == first)
        self.assertEqual(updated['previous_choice'], 3)
        self.client.post(f'/api/chapters/{first}/attempts', json={'question_index': 0, 'choice': 0})
        with TestClient(app) as reopened:
            rows = reopened.get('/api/review').json()
        self.assertFalse(any(row['chapter_id'] == first for row in rows))
        self.assertTrue(any(row['chapter_id'] == second for row in rows))

    def test_backup_roundtrip_preserves_learning_data_and_excludes_settings(self):
        chapter_id = self.generate(chapter='学习备份测试')['chapter_id']
        url = '/api/chapters/' + chapter_id
        note = '  保留原始笔记格式。\n第二行。\n'
        self.client.put(url + '/notes', json={'notes': note})
        self.client.post(url + '/attempts', json={'question_index': 0, 'choice': 1})
        self.client.put(url + '/progress', json={'completed': True})
        self.client.post(url + '/questions', json={'question': '请解释这道题。'})
        exported = self.client.get('/api/backups/export')
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertNotIn('test-only-key', exported.text)
        self.assertNotIn('base_url', exported.text)
        document = exported.json()
        self.assertEqual(set(document), {'application', 'format_version', 'created_at', 'chapters', 'rewards', 'practice_days'})
        self.assertEqual(document['format_version'], 2)
        chapter = next(item for item in document['chapters'] if item['id'] == chapter_id)
        self.assertEqual(chapter['notes'], note)
        self.assertTrue(chapter['completed'])
        self.assertEqual(len(chapter['messages']), 1)
        chapter['id'] = 'restored-roundtrip'
        document['chapters'] = [chapter]
        content = json.dumps(document, ensure_ascii=False).encode('utf-8')
        files = {'file': ('backup.json', content, 'application/json')}
        preview = self.client.post('/api/backups/preview', files=files)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['new'], 1)
        restored = self.client.post('/api/backups/import', files=files)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['imported'], 1)
        detail = self.client.get('/api/chapters/restored-roundtrip').json()
        self.assertEqual(detail['notes'], note)
        self.assertEqual(detail['notes_revision'], 0)
        self.assertEqual(detail['guide'], chapter['guide'])
        self.assertEqual(detail['messages'][0]['answer'], chapter['messages'][0]['answer'])
        self.assertFalse(detail['attempts'][0]['correct'])
        self.assertTrue(self.client.get('/api/settings').json()['has_key'])

    def test_backup_conflicts_create_copies_and_repeated_imports_do_not_overwrite(self):
        chapter_id = self.generate(chapter='有新旧笔记的章节')['chapter_id']
        url = '/api/chapters/' + chapter_id
        self.client.put(url + '/notes', json={'notes': '备份时的笔记'})
        document = self.client.get('/api/backups/export').json()
        document['chapters'] = [item for item in document['chapters'] if item['id'] == chapter_id]
        files = {'file': ('backup.json', json.dumps(document).encode(), 'application/json')}
        self.client.put(url + '/notes', json={'notes': '后来继续学习的新笔记', 'expected_revision': 1})
        preview = self.client.post('/api/backups/preview', files=files).json()
        self.assertEqual(preview['copies'], 1)
        result = self.client.post('/api/backups/import', files=files)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['copies'], 1)
        copied_id = result.json()['chapter_ids'][0]
        self.assertNotEqual(copied_id, chapter_id)
        self.assertEqual(self.client.get(url).json()['notes'], '后来继续学习的新笔记')
        copy = self.client.get('/api/chapters/' + copied_id).json()
        self.assertEqual(copy['notes'], '备份时的笔记')
        self.assertTrue(copy['title'].endswith('（导入副本）'))
        self.client.put('/api/chapters/' + copied_id + '/notes', json={'notes': '导入副本里又做了补充'})
        with TestClient(app) as reopened:
            repeated = reopened.post('/api/backups/import', files=files)
        self.assertEqual(repeated.json()['imported'], 0)
        self.assertEqual(repeated.json()['skipped'], 1)
        self.assertEqual(self.client.get('/api/chapters/' + copied_id).json()['notes'], '导入副本里又做了补充')

    def test_invalid_backup_is_rejected_without_partial_import(self):
        import copy
        document = self.client.get('/api/backups/export').json()
        valid = next(item for item in document['chapters'] if item['id'] == 'demo-network-layer')
        valid['id'] = 'would-be-valid-import'
        invalid = copy.deepcopy(valid)
        invalid['id'] = 'invalid-second-chapter'
        invalid['guide']['knowledge_points'][0]['source_ids'] = ['missing-source']
        document['chapters'] = [valid, invalid]
        before = len(self.client.get('/api/chapters').json())
        invalid_versions = [document, {**document, 'format_version': 99}, {**document, 'chapters': [valid, valid]}]
        wrong_result = copy.deepcopy(valid)
        wrong_result['attempts'] = [{'question_index': 0, 'choice': 1, 'correct': True, 'created_at': now()}]
        invalid_versions.append({**document, 'chapters': [wrong_result]})
        for value in invalid_versions:
            result = self.client.post('/api/backups/import', files={'file': ('bad.json', json.dumps(value).encode())})
            self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(len(self.client.get('/api/chapters').json()), before)
        self.assertEqual(self.client.get('/api/chapters/would-be-valid-import').status_code, 404)
        invalid_json = self.client.post('/api/backups/preview', files={'file': ('bad.json', b'not json')})
        self.assertEqual(invalid_json.status_code, 422)
        too_large = self.client.post('/api/backups/import', content=b'x', headers={'content-length': str(31 * 1024 * 1024)})
        self.assertEqual(too_large.status_code, 413)
        cross_origin = self.client.post('/api/backups/import', files={'file': ('backup.json', b'{}')}, headers={'Origin': 'https://unrelated.example'})
        self.assertEqual(cross_origin.status_code, 403)

    def test_backup_database_failure_rolls_back_all_new_records(self):
        import copy
        document = self.client.get('/api/backups/export').json()
        source = next(item for item in document['chapters'] if item['id'] == 'demo-network-layer')
        first, second = copy.deepcopy(source), copy.deepcopy(source)
        first['id'], second['id'] = 'rollback-first', 'rollback-second'
        document['chapters'] = [first, second]
        files = {'file': ('backup.json', json.dumps(document).encode())}
        with db() as conn:
            conn.execute("""CREATE TRIGGER reject_backup_second BEFORE INSERT ON chapters
                WHEN NEW.id='rollback-second' BEGIN SELECT RAISE(ABORT,'intentional test failure'); END""")
        try:
            result = self.client.post('/api/backups/import', files=files)
            self.assertEqual(result.status_code, 422)
            self.assertIn('回滚', result.json()['detail'])
            with db() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM chapters WHERE id IN ('rollback-first','rollback-second')").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_imports WHERE source_id IN ('rollback-first','rollback-second')").fetchone()[0], 0)
        finally:
            with db() as conn:
                conn.execute('DROP TRIGGER reject_backup_second')
        retried = self.client.post('/api/backups/import', files=files)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()['imported'], 2)

    def test_invalid_input_and_cross_origin_mutations_are_rejected(self):
        result = self.client.put("/api/settings", json={"base_url": "https://example.com/v1", "model": "x"}, headers={"Origin": "https://unrelated.example"})
        self.assertEqual(result.status_code, 403)
        for value in ({"course": "   ", "chapter": "x", "mode": "topic"}, {"course": "x", "chapter": "x", "mode": "material", "text": "too short"}):
            self.assertEqual(self.client.post("/api/chapters/generate", json=value).status_code, 422)
        self.assertEqual(self.client.post("/api/chapters/demo-network-layer/attempts", json={"question_index": 999, "choice": 1}).status_code, 422)
        self.assertEqual(self.client.post("/api/materials", files={"file": ("bad.exe", b"not a document")}).status_code, 422)
        self.assertEqual(self.client.post("/api/materials", files={"file": ("bad.jpg", b"not an image")}).status_code, 422)
        self.assertEqual(self.client.get("/data/settings.json").status_code, 404)
        ambiguous = self.client.post('/api/chapters/generate', json={'course': '网络', 'chapter': '第三章', 'mode': 'topic'})
        self.assertEqual(ambiguous.status_code, 422)
        self.assertIn('章节名称', ambiguous.text)

    def test_gb18030_and_screenshot_import(self):
        text = "中文笔记：网络层负责跨网络的分组转发与寻址。" * 4
        result = self.client.post("/api/materials", files={"file": ("note.txt", text.encode("gb18030"))})
        self.assertEqual(result.json()["preview"], text)
        # The provider double verifies the multimodal request contract, not OCR quality.
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nc9sAAAAASUVORK5CYII=")
        image = self.client.post("/api/materials", files={"file": ("screen.png", png, "image/png")})
        self.assertEqual(image.status_code, 200, image.text)
        self.assertIn("核对", image.json()["warnings"][0])
        message = self.provider.calls[-1]["payload"]["messages"][-1]["content"]
        self.assertTrue(message[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_stale_note_write_cannot_overwrite_another_window(self):
        job = self.generate()
        url = "/api/chapters/" + job["chapter_id"]
        self.assertEqual(self.client.get(url).json()["notes_revision"], 0)
        first = self.client.put(url + "/notes", json={"notes": "窗口一的笔记", "expected_revision": 0})
        self.assertEqual(first.json()["notes_revision"], 1)
        stale = self.client.put(url + "/notes", json={"notes": "窗口二的笔记", "expected_revision": 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(url).json()["notes"], "窗口一的笔记")
        merged = self.client.put(url + "/notes", json={"notes": "窗口二的笔记\n窗口一的笔记", "expected_revision": 1})
        self.assertEqual(merged.json()["notes_revision"], 2)

    def test_corrupt_settings_can_be_repaired_without_exposing_contents(self):
        path = Path(TEST_DIRECTORY.name) / "settings.json"
        path.write_text('["invalid", "sensitive-example"]', encoding="utf-8")
        result = self.client.get("/api/settings")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["configured"])
        self.assertTrue(result.json()["config_error"])
        self.assertNotIn("sensitive-example", result.text)
        repaired = self.client.put("/api/settings", json={"base_url": self.provider.base_url, "model": "test-model", "api_key": "new-test-key"})
        self.assertEqual(repaired.status_code, 200)
        self.assertTrue(repaired.json()["configured"])

    def test_interrupted_job_is_marked_failed_on_restart(self):
        with db() as conn:
            conn.execute("INSERT INTO jobs (id,status,stage,created_at) VALUES (?,?,?,?)", ("interrupted-test", "running", "正在读取", now()))
        with TestClient(app) as reopened:
            job = reopened.get("/api/jobs/interrupted-test").json()
        self.assertEqual(job["status"], "failed")
        self.assertIn("上次关闭", job["error"])

    def test_empty_provider_configuration_has_a_clear_failure(self):
        path = Path(TEST_DIRECTORY.name) / "settings.json"
        path.write_text('{}', encoding="utf-8")
        response = self.client.post("/api/chapters/generate", json={"course": "网络", "chapter": "网络层", "mode": "topic"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("模型设置", response.json()["detail"])
        self.assertEqual(self.provider.calls, [])

    def test_real_pdf_extraction_respects_selected_chapter_pages(self):
        writer = PdfWriter()
        texts = ["Unselected chapter one.", "The network layer forwards IP packets across networks using destination addresses.", "Unselected chapter three."]
        for text in texts:
            page = writer.add_blank_page(width=600, height=800)
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 760 Td ({text}) Tj ET".encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        uploaded = self.client.post('/api/materials', files={'file': ('network-book.pdf', buffer.getvalue(), 'application/pdf')})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()['page_count'], 3)
        job = self.generate(mode='material', material_id=uploaded.json()['id'], page_start=2, page_end=2)
        self.assertEqual(job['status'], 'completed', job)
        chapter = self.client.get('/api/chapters/' + job['chapter_id']).json()
        self.assertEqual(len(chapter['sources']), 1)
        self.assertEqual(chapter['sources'][0]['text'], texts[1])
        self.assertIn('第 2 页', chapter['sources'][0]['label'])
        self.assertNotIn('Unselected', json.dumps(self.provider.calls))

    def test_pdf_bookmarks_propose_ranges_without_summarizing_other_chapters(self):
        from tests.pdf_fixtures import PAGE_TEXTS, bookmarked_pdf
        result = self.client.post('/api/materials', files={'file': ('bookmarked.pdf', bookmarked_pdf(), 'application/pdf')})
        self.assertEqual(result.status_code, 200, result.text)
        material = result.json()
        outline = material['outline']
        self.assertEqual([(item['level'], item['page_start'], item['page_end']) for item in outline], [(0, 1, 3), (1, 1, 1), (1, 2, 3), (0, 4, 4)])
        self.assertEqual(outline[0]['title'], '第一章 网络层')
        self.assertTrue(all(item['range_inferred'] for item in outline))
        self.assertTrue(any('估算' in warning for warning in material['warnings']))
        selected = outline[2]
        job = self.generate(mode='material', material_id=material['id'], chapter=selected['title'], page_start=selected['page_start'], page_end=selected['page_end'])
        self.assertEqual(job['status'], 'completed', job)
        chapter = self.client.get('/api/chapters/' + job['chapter_id']).json()
        self.assertEqual([source['text'] for source in chapter['sources']], PAGE_TEXTS[1:3])
        self.assertEqual([source['id'] for source in chapter['sources']], ['p2-s1', 'p3-s1'])

    def test_irregular_pdf_bookmarks_only_locate_start_pages(self):
        from tests.pdf_fixtures import bookmarked_pdf
        result = self.client.post('/api/materials', files={'file': ('irregular.pdf', bookmarked_pdf(irregular=True), 'application/pdf')})
        self.assertEqual(result.status_code, 200, result.text)
        outline = result.json()['outline']
        self.assertEqual([item['page_start'] for item in outline], [4, 1])
        self.assertTrue(all(not item['range_inferred'] and item['page_start'] == item['page_end'] for item in outline))
        self.assertTrue(any('仅定位起始页' in warning for warning in result.json()['warnings']))

    def test_followup_retrieval_keeps_chinese_terms_and_matches_whole_english_words(self):
        import asyncio
        from backend import ai

        for question, distractor, relevant in (
            ('请解释拥塞窗口', '无关的背景资料。', '拥塞控制会调整窗口，窗口限制未确认的数据量。'),
            ('解释 TCP', 'tcpip 并不是本题要匹配的独立单词。', 'TCP provides a byte stream.'),
        ):
            with self.subTest(question=question):
                chapter = self.client.get('/api/chapters/demo-network-layer').json()
                chapter['sources'] = [
                    {'id': f'p{index}-s1', 'label': f'片段 {index}', 'text': (distractor * 1000)[:6000]}
                    for index in range(1, 4)
                ] + [{'id': 'p4-s1', 'label': '问题所需片段', 'text': (relevant * 1000)[:6000]}]
                asyncio.run(ai.explain_question(chapter, question, ai.configured_settings()))
                system = self.provider.calls[-1]['payload']['messages'][0]['content']
                context = json.loads(system.rsplit('\n', 1)[-1])
                self.assertEqual(context['sources'][0]['id'], 'p4-s1')
                self.assertLessEqual(sum(len(item['text']) for item in context['sources']), 18000)

    def test_protected_corrupt_and_oversize_files_fail_clearly(self):
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        writer.encrypt('test-password')
        buffer = io.BytesIO()
        writer.write(buffer)
        protected = self.client.post('/api/materials', files={'file': ('protected.pdf', buffer.getvalue())})
        self.assertEqual(protected.status_code, 422)
        self.assertIn('密码保护', protected.json()['detail'])
        corrupt = self.client.post('/api/materials', files={'file': ('corrupt.pdf', b'not a pdf')})
        self.assertEqual(corrupt.status_code, 422)
        too_large = self.client.post('/api/materials', content=b'x', headers={'content-length': str(21 * 1024 * 1024)})
        self.assertEqual(too_large.status_code, 413)


if __name__ == "__main__":
    unittest.main(verbosity=2)
