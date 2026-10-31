import json
import io
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import latte


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.path.joinpath("profile.json").write_text(json.dumps({
            "facts": [{"key": "experience_design", "label": "経験", "value": "画像制作", "status": "confirmed", "source": "本人"}],
            "preferences": {"remote_days": 2, "min_monthly_salary": 250000},
        }), encoding="utf-8")
        self.store = latte.Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_and_preferences_persist_without_overwriting_facts(self):
        self.store.update_preferences({"remote_days": 3})
        self.path.joinpath("profile.json").write_text("{}")
        state = latte.Store(self.path).state()
        self.assertEqual(state["profile"]["preferences"]["remote_days"], 3)
        self.assertEqual(state["profile"]["facts"][0]["value"], "画像制作")

    def test_unknown_salary_is_not_assumed_and_mismatch_requires_evidence(self):
        state = self.store.add_job({"title": "デザイナー", "company": "入力企業"})
        evaluation = state["jobs"][0]["evaluation"]
        self.assertEqual(evaluation["fit"], "check")
        self.assertTrue(any("給与" in value for value in evaluation["unknowns"]))
        state = self.store.add_job({"title": "別の求人", "remote_days": 0, "salary_min": 200000, "salary_max": 220000})
        job = next(job for job in state["jobs"] if job["title"] == "別の求人")
        self.assertEqual(job["evaluation"]["fit"], "mismatch")

    def test_prepare_is_idempotent_and_never_applies(self):
        job = self.store.add_job({"title": "制作担当"})["jobs"][0]
        self.store.prepare(job["id"])
        state = self.store.prepare(job["id"])
        self.assertEqual(len(state["tasks"]), 3)
        self.assertEqual(len(state["drafts"]), 1)
        self.assertEqual(state["jobs"][0]["status"], "preparing")
        self.assertIn("未提出", state["drafts"][0]["body"])
        self.assertIn("画像制作", state["drafts"][0]["body"])
        task_id = state["tasks"][0]["id"]
        self.store.toggle_task(task_id)
        self.assertEqual(next(task for task in self.store.state()["tasks"] if task["id"] == task_id)["status"], "done")

    def test_daily_checkin_persists_and_reduces_next_step_when_tired(self):
        state = self.store.checkin({"energy": "low", "note": "今日は休みたい"})
        self.assertEqual(state["checkins"][-1]["note"], "今日は休みたい")
        response = self.store.chat({"message": "次に何をしよう？"})
        self.assertIn("新しいタスクを増やさず", response["message"]["content"])
        self.assertEqual(latte.Store(self.path).state()["checkins"][-1]["energy"], "low")

    def test_old_checkin_does_not_make_today_tired(self):
        with patch("latte.now", return_value="2000-01-01T08:00:00+00:00"):
            self.store.checkin({"energy": "low"})
        result = self.store.chat({"message": "次に何をしよう？"})
        self.assertNotIn("さっき", result["message"]["content"])
        self.assertNotIn("新しいタスクを増やさず", result["message"]["content"])

    def test_memories_can_be_added_by_chat_and_deleted_without_reseeding(self):
        with patch.dict("os.environ", {"LATTE_OPENAI_API_KEY": "test-do-not-use"}), patch("latte.ai_reply") as ai:
            self.store.ai_consent({"enabled": True})
            response = self.store.chat({"message": "覚えて：得意なことは画像の色補正"})
            self.assertIn("保存した", response["message"]["content"])
            ai.assert_not_called()
        fact = self.store.state()["profile"]["facts"][-1]
        self.assertEqual(fact["status"], "reported")
        self.assertEqual(fact["source"], "このアプリでの会話")
        self.store.delete_memory(fact["key"])
        self.store.delete_memory("experience_design")
        self.assertEqual(latte.Store(self.path).state()["profile"]["facts"], [])

    def test_reported_experience_is_used_but_unverified_business_is_excluded(self):
        with self.store.db() as db:
            profile = self.store.one(db, "profile", "main")
            profile["facts"][0]["status"] = "reported"
            profile["facts"].append({"key": "aio", "label": "未確認事業", "value": "架空の顧客成果", "status": "reported"})
            self.store.put(db, "profile", "main", profile)
        result = self.store.chat({"message": "私の強みを整理して"})
        self.assertIn("画像制作", result["message"]["content"])
        self.assertNotIn("架空の顧客成果", result["message"]["content"])
        job = self.store.add_job({"title": "制作"})["jobs"][0]
        draft = self.store.prepare(job["id"])["drafts"][0]["body"]
        self.assertIn("画像制作", draft)
        self.assertIn("本人申告", draft)
        self.assertNotIn("架空の顧客成果", draft)

    def test_substantive_cautions_prevent_promising_label(self):
        base = {"title": "制作", "description": "本文", "company": "入力企業", "creative_ratio": 80,
                "remote_days": 2, "salary_min": 300000, "location": "入力勤務地", "employment": "契約社員"}
        prefs = {**latte.PREFS, "remote_days": 2, "min_monthly_salary": 250000}
        self.assertEqual(latte.evaluate(latte.validate_job(base), prefs)["fit"], "promising")
        for delta in ({"creative_ratio": 20}, {"salary_min": 200000}, {"salary_min": 200000, "salary_max": 300000}):
            with self.subTest(delta=delta):
                self.assertEqual(latte.evaluate(latte.validate_job({**base, **delta}), prefs)["fit"], "check")

    def test_key_without_consent_makes_no_network_call(self):
        with patch.dict("os.environ", {"LATTE_OPENAI_API_KEY": "test-do-not-use"}), patch("latte.ai_reply") as ai:
            result = self.store.chat({"message": "次に何をする？"})
            self.assertEqual(result["mode"], "guide")
            ai.assert_not_called()
            self.store.ai_consent({"enabled": True})
            self.assertFalse(self.store.state()["connection"]["verified"])
            ai.return_value = "確認済みの回答"
            result = self.store.chat({"message": "相談したい"})
            self.assertEqual(result["mode"], "ai")
            ai.assert_called_once()
            self.assertTrue(self.store.state()["connection"]["verified"])
            self.store.ai_consent({"enabled": False})
            self.assertEqual(self.store.state()["connection"]["mode"], "guide")

    def test_ai_bad_responses_are_explicit_errors(self):
        for body in (b"not-json", b"[]", b'{"output": "bad"}', b'{"output": [{"type":"message","content":null}]}'):
            with self.subTest(body=body), patch.dict("os.environ", {"LATTE_OPENAI_API_KEY": "test-do-not-use"}), patch("latte.urllib.request.urlopen", return_value=io.BytesIO(body)):
                with self.assertRaises(latte.InputError):
                    latte.ai_reply(self.store.state())

    def test_bad_inputs_are_rejected(self):
        for payload in ({"title": "求人", "url": "javascript:alert(1)"},
                        {"title": "求人", "url": "https://a:b@example.com"},
                        {"title": "求人", "salary_min": 400000, "salary_max": 300000},
                        {"title": "求人", "remote_days": True}, {"title": ""}):
            with self.subTest(payload=payload), self.assertRaises(latte.InputError):
                self.store.add_job(payload)
        with self.assertRaises(latte.InputError):
            self.store.update_preferences({"remote_days": 6})


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = latte.Store(Path(self.tmp.name) / "data")
        web = Path(self.tmp.name) / "web"
        web.mkdir()
        web.joinpath("index.html").write_text("<h1>LATTE</h1>")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), latte.make_handler(self.store, web))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_state_and_real_mutation(self):
        with self.client.open(self.base + "/api/health") as response:
            self.assertTrue(json.load(response)["ok"])
        req = urllib.request.Request(self.base + "/api/jobs", data=json.dumps({"title": "制作"}).encode(), headers={"Content-Type": "application/json"})
        with self.client.open(req) as response:
            self.assertEqual(json.load(response)["jobs"][0]["title"], "制作")

    def test_origin_host_and_path_boundaries(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
            req = urllib.request.Request(self.base + "/api/state", headers=headers)
            with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                self.client.open(req)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.client.open(self.base + "/%2e%2e/data/latte.db")
        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
