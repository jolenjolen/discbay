import base64
import os
import shutil
import tempfile
import time
import unittest

try:
    from discbay import studio
except ImportError:  # Flask not installed
    studio = None

from discbay import discmaker, drive

PNG = base64.b64decode(  # 1x1 transparent PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
HOST = {"Host": "localhost:8404"}
POST = {**HOST, "X-DiscBay-Studio": "1"}


@unittest.skipIf(studio is None, "Flask not installed")
class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.device = os.path.join(self.tmp, "disc.img")
        studio.app.config.update(DEVICE=self.device, PORT=8404, TESTING=True)
        self.client = studio.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page_and_assets_load(self):
        for path in ("/", "/static/studio.css", "/static/studio.js"):
            self.assertEqual(self.client.get(path, headers=HOST).status_code, 200, path)

    def test_blocks_other_hosts_and_sites(self):
        self.assertEqual(self.client.get("/api/status", headers={"Host": "evil.example:8404"}).status_code, 403)
        self.assertEqual(self.client.post("/api/check", json={}, headers=HOST).status_code, 403)
        evil = {**POST, "Origin": "https://evil.example"}
        self.assertEqual(self.client.post("/api/check", json={}, headers=evil).status_code, 403)
        same = {**POST, "Origin": "http://localhost:8404"}
        self.assertEqual(self.client.post("/api/check", json={}, headers=same).status_code, 200)

    def test_icon_ids_are_validated(self):
        self.assertEqual(self.client.get("/api/icon/..%2Fetc", headers=HOST).status_code, 404)

    def test_check_reports_problems_plainly(self):
        r = self.client.post("/api/check", json={"kind": "steam", "target": "abc", "name": "X"}, headers=POST).get_json()
        self.assertFalse(r["ok"])
        self.assertIn("number", r["error"])
        r = self.client.post("/api/check", json={"kind": "command", "target": "true", "name": "Open TTD!"},
                             headers=POST).get_json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["label"], "OPEN_TTD")

    def test_rejects_fake_cover(self):
        r = self.client.post("/api/burn", headers=POST, json={
            "kind": "command", "target": "true", "name": "X", "cover": "data:image/png;base64,QUJD"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("valid image", r.get_json()["error"])

    @unittest.skipUnless(shutil.which("xorriso"), "xorriso not installed")
    def test_burn_then_rewrite_through_the_api(self):
        cover = "data:image/png;base64," + base64.b64encode(PNG).decode()
        for name, target in (("First", "true"), ("Second", "false")):
            r = self.client.post("/api/burn", headers=POST, json={
                "kind": "command", "target": target, "name": name, "cover": cover, "close_on_eject": True})
            self.assertEqual(r.status_code, 200, r.get_json())
            job_id = r.get_json()["job"]
            for _ in range(200):
                job = self.client.get(f"/api/jobs/{job_id}", headers=HOST).get_json()
                if job["state"] != "running":
                    break
                time.sleep(0.05)
            self.assertEqual(job["state"], "done", job.get("error"))
        self.assertEqual(drive.read_label(self.device), "SECOND")
        self.assertTrue(job["result"]["updated"])
        status = self.client.get("/api/status", headers=HOST).get_json()
        self.assertEqual(status["media"]["state"], "appendable")
        self.assertEqual(status["current"], "Second")

    def test_iso_download(self):
        r = self.client.post("/api/iso", headers=POST, json={"kind": "command", "target": "true", "name": "Iso Test"})
        self.assertEqual(r.status_code, 200)
        path = os.path.join(self.tmp, "out.iso")
        with open(path, "wb") as f:
            f.write(r.data)
        self.assertEqual(drive.read_label(path), "ISO_TEST")


class LabelTests(unittest.TestCase):
    def test_label_from_name(self):
        self.assertEqual(discmaker.label_from_name("OpenTTD 14.1"), "OPENTTD_14_1")
        self.assertEqual(discmaker.label_from_name("!!!"), "DISC")
        self.assertEqual(len(discmaker.label_from_name("x" * 50)), 32)


if __name__ == "__main__":
    unittest.main()
