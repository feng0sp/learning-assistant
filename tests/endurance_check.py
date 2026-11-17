"""Long-running local reliability check, isolated from user data and real model APIs.

This exercises an open browser, persisted notes and attempts, model errors, and recovery
across app restarts. It is opt-in; it is not part of the normal quick test suite.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix="zhizhang-endurance-")
os.environ["STUDY_DATA_DIR"] = TEST_DIRECTORY.name
for key in ("STUDY_API_BASE_URL", "STUDY_API_KEY", "STUDY_MODEL"):
    os.environ.pop(key, None)

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.main import app
from backend.storage import db
from tests.fake_provider import FakeProvider
from tests.pdf_fixtures import PAGE_TEXTS, bookmarked_pdf

OUTPUT = Path(__file__).resolve().parent.parent / "test-results"
OUTPUT.mkdir(exist_ok=True)


class LocalServer:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(.05)
        raise RuntimeError("Local server failed to start")

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=15)
        self.sock.close()
        if self.thread.is_alive():
            raise RuntimeError("Server did not shut down")


def working_set_mb():
    if os.name != "nt":
        return None
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return round(counters.WorkingSetSize / 1024 / 1024, 2)


def run(until, interval, report_name="endurance-report"):
    provider = FakeProvider().start()
    server = LocalServer()
    started = datetime.now(timezone.utc)
    source_files = sorted((OUTPUT.parent / "backend").glob("*.py")) + sorted((OUTPUT.parent / "dist").glob("*"))
    def fingerprint():
        return hashlib.sha256(b"".join(file.read_bytes() for file in source_files if file.is_file())).hexdigest()
    report = {"status": "running", "started_at": started.isoformat(), "scheduled_end": until.isoformat(), "cycles": [], "errors": [], "real_model_used": False, "source_fingerprint": fingerprint()}
    output = OUTPUT / (report_name + ".json")
    def checkpoint():
        report["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output)
    checkpoint()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            cycle = 0
            backup_snapshot = None
            material_id = None
            while datetime.now(timezone.utc) < until:
                if (OUTPUT / "endurance-stop.flag").exists():
                    report["status"] = "stopped"
                    break
                began = time.monotonic()
                cycle += 1
                if cycle > 1 and cycle % 12 == 0:
                    server.close()
                    server = LocalServer()
                with httpx.Client(base_url=server.url, timeout=20, trust_env=False) as client:
                    assert client.get("/api/health").json()["status"] == "ok"
                    client.put("/api/settings", json={"base_url": provider.base_url, "model": "endurance-test-double", "api_key": "test-only"}).raise_for_status()
                    if cycle % 6 == 0:
                        provider.mode = "rate_limited"
                        failure = client.post("/api/settings/test")
                        assert failure.status_code == 422 and "限流" in failure.json()["detail"]
                    provider.mode = "normal"
                    client.post("/api/settings/test").raise_for_status()
                    if material_id is None:
                        uploaded = client.post('/api/materials', files={'file': ('chapter.pdf', bookmarked_pdf(), 'application/pdf')})
                        uploaded.raise_for_status()
                        material = uploaded.json()
                        material_id = material['id']
                        assert (material['outline'][2]['page_start'], material['outline'][2]['page_end']) == (2, 3)
                    assert client.get(f'/api/materials/{material_id}/pages/3').json()['text'] == PAGE_TEXTS[2]
                    chapter_id = "demo-network-layer"
                    if cycle % 10 == 0:
                        response = client.post("/api/chapters/generate", json={"course": "计算机网络", "chapter": f"耐久验证 {cycle}", "mode": "material", "material_id": material_id, "page_start": 2, "page_end": 3})
                        response.raise_for_status()
                        job_id = response.json()["job_id"]
                        for _ in range(100):
                            job = client.get("/api/jobs/" + job_id).json()
                            if job["status"] in {"completed", "failed"}:
                                break
                            time.sleep(.1)
                        assert job["status"] == "completed", job
                        chapter_id = job["chapter_id"]
                        generated = client.get('/api/chapters/' + chapter_id).json()
                        assert [source['text'] for source in generated['sources']] == PAGE_TEXTS[1:3]
                    mobile = cycle % 3 == 0
                    page.set_viewport_size({'width': 390 if mobile else 1280, 'height': 844 if mobile else 900})
                    page.goto(server.url + "/#chapter/" + chapter_id)
                    expect(page.locator("#export-link")).to_have_attribute("href", "/api/chapters/" + chapter_id + "/export")
                    expect(page.locator(".knowledge-card")).to_have_count(4)
                    note = f"持续使用验证，第 {cycle} 次保存。中文、English、公式 2^8 = 256。"
                    page.locator("#notes").fill(note)
                    page.locator("#save-notes").click()
                    expect(page.locator("#notes-status")).to_have_text("已保存到本机")
                    page.get_by_role("tab", name="自测练习").click()
                    page.locator(f'input[name="quiz-0"][value="{cycle % 4}"]').check()
                    page.locator('[data-check="0"]').click()
                    expect(page.locator("#quiz-0 .quiz-feedback")).to_be_visible()
                    page.reload()
                    expect(page.locator("#notes")).to_have_value(note)
                    saved = client.get("/api/chapters/" + chapter_id).json()
                    assert saved["notes"] == note
                    handout = client.get('/api/chapters/' + chapter_id + '/print')
                    handout.raise_for_status()
                    assert note in handout.text and '自测答案与解析' in handout.text
                    assert any(attempt["choice"] == cycle % 4 and attempt["question_index"] == 0 for attempt in saved["attempts"])
                    review = client.get('/api/review').json()
                    assert any(item['chapter_id'] == chapter_id and item['question_index'] == 0 for item in review) == (cycle % 4 != 0)
                    if cycle % 3 == 0:
                        page.locator('#toggle-navigation').click()
                        page.locator('#open-review').click()
                        expect(page.locator('#review-dialog')).to_be_visible()
                        page.locator('[data-close="review-dialog"]').click()
                        page.locator('#toggle-navigation').click()
                    if cycle % 5 == 0:
                        page.locator('#question').fill(f'第 {cycle} 轮：请解释这一章的主要概念。')
                        page.locator('#ask-button').click()
                        expect(page.locator('#ask-button')).to_be_enabled()
                        expect(page.locator('#messages .answer').last).to_be_visible()
                        expect(page.locator('#question')).to_have_value('')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal page overflow'
                    if backup_snapshot is not None:
                        restored = client.post('/api/backups/import', files={'file': ('previous.json', backup_snapshot, 'application/json')})
                        restored.raise_for_status()
                        assert restored.json()['imported'] == 0
                        assert client.get('/api/chapters/' + chapter_id).json()['notes'] == note
                    if cycle % 4 == 0:
                        snapshot_response = client.get('/api/backups/export')
                        snapshot_response.raise_for_status()
                        backup_snapshot = snapshot_response.content
                        restored = client.post('/api/backups/import', files={'file': ('current.json', backup_snapshot, 'application/json')})
                        restored.raise_for_status()
                        assert restored.json()['imported'] == 0
                    assert "test-only" not in client.get("/api/settings").text
                    assert not report["errors"], report["errors"]
                    with db() as conn:
                        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
                    assert integrity == "ok", integrity
                    report["cycles"].append({"cycle": cycle, "at": datetime.now(timezone.utc).isoformat(), "duration_seconds": round(time.monotonic() - began, 2), "python_working_set_mb": working_set_mb(), "database_check": integrity, "viewport_width": 390 if mobile else 1280, "server_restarted": cycle > 1 and cycle % 12 == 0})
                    checkpoint()
                    print(f"Cycle {cycle}: passed; elapsed={(datetime.now(timezone.utc)-started).total_seconds():.0f}s", flush=True)
                delay = min(interval, max(0, (until - datetime.now(timezone.utc)).total_seconds()))
                # Keep the browser's event loop alive while the application remains open.
                page.wait_for_timeout(delay * 1000)
            browser.close()
        if report["status"] == "running":
            report["status"] = "passed"
        report["elapsed_seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
        report["source_changed_during_run"] = fingerprint() != report["source_fingerprint"]
        checkpoint()
    except BaseException as exc:
        report["status"] = "failed"
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        checkpoint()
        raise
    finally:
        server.close()
        provider.close()
        TEST_DIRECTORY.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--until-utc", required=True)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--report-name", default="endurance-report")
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.until_utc)
    if deadline.tzinfo is None or args.interval <= 0 or deadline <= datetime.now(timezone.utc):
        parser.error("Use a timezone-aware deadline and a positive interval")
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.report_name):
        parser.error("Report name must contain only letters, digits, underscores or hyphens")
    run(deadline, args.interval, args.report_name)
