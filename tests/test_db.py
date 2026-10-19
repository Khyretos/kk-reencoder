"""Tests for app/rx_db.py (SQLite state, in memory)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_db as db  # noqa: E402


def rec(path, folder, size=1000, mtime=1.0, status="pending", reason=None, library="Anime"):
    return {"path": path, "library": library, "folder": folder, "size": size, "mtime": mtime,
            "codec": "h264", "height": 1080, "bitrate_kbps": 8000, "duration_s": 1400.0,
            "status": status, "reason": reason}


class Db(unittest.TestCase):
    def setUp(self):
        self.c = db.connect(":memory:")
        db.upsert_file(self.c, rec("/m/A/Zeta/1.mkv", "Zeta"))
        db.upsert_file(self.c, rec("/m/A/alpha/2.mkv", "alpha"))
        db.upsert_file(self.c, rec("/m/A/alpha/1.mkv", "alpha"))
        db.upsert_file(self.c, rec("/m/A/beta/1.mkv", "beta", status="skipped", reason="already AV1"))

    def test_next_file_alphabetical(self):
        self.assertEqual(db.next_file(self.c)["path"], "/m/A/alpha/1.mkv")

    def test_priority_and_pause(self):
        db.set_folder(self.c, "Anime", "Zeta", priority=5)
        self.assertEqual(db.next_file(self.c)["path"], "/m/A/Zeta/1.mkv")
        db.set_folder(self.c, "Anime", "Zeta", paused=True)
        self.assertEqual(db.next_file(self.c)["path"], "/m/A/alpha/1.mkv")

    def test_none_when_nothing_pending(self):
        for p in ("/m/A/Zeta/1.mkv", "/m/A/alpha/1.mkv", "/m/A/alpha/2.mkv"):
            db.set_result(self.c, p, status="predicted", new_bytes=400, vmaf=95.5, quality=24)
        self.assertIsNone(db.next_file(self.c))

    def test_rescan_keeps_result_when_unchanged(self):
        db.set_result(self.c, "/m/A/alpha/1.mkv", status="predicted", new_bytes=400, vmaf=95.5, quality=24)
        db.upsert_file(self.c, rec("/m/A/alpha/1.mkv", "alpha"))
        self.assertEqual(db.get_file(self.c, "/m/A/alpha/1.mkv")["status"], "predicted")

    def test_rescan_resets_when_file_changed(self):
        db.set_result(self.c, "/m/A/alpha/1.mkv", status="predicted", new_bytes=400, vmaf=95.5, quality=24)
        db.upsert_file(self.c, rec("/m/A/alpha/1.mkv", "alpha", size=2000, mtime=2.0))
        f = db.get_file(self.c, "/m/A/alpha/1.mkv")
        self.assertEqual(f["status"], "pending")
        self.assertIsNone(f["new_bytes"])
        self.assertEqual(f["size"], 2000)

    def test_rescan_keeps_encoded(self):
        # after a replace the file on disk is the AV1 one; the scanner sees a new size but must
        # not forget the original size or reset the status
        db.set_result(self.c, "/m/A/alpha/1.mkv", status="encoded", new_bytes=400, vmaf=95.5, quality=24)
        db.upsert_file(self.c, dict(rec("/m/A/alpha/1.mkv", "alpha", size=400, mtime=3.0), codec="av1",
                                    status="skipped", reason="already AV1"))
        f = db.get_file(self.c, "/m/A/alpha/1.mkv")
        self.assertEqual(f["status"], "encoded")
        self.assertEqual(f["size"], 1000)

    def test_staged_rows_survive_rescan_and_count(self):
        db.set_result(self.c, "/m/A/alpha/1.mkv", status="confirmed", new_bytes=300, vmaf=95.5, quality=22)
        db.set_result(self.c, "/m/A/alpha/2.mkv", status="staged", new_bytes=200, vmaf=95.5, quality=22)
        # after confirm the file in place is the smaller re-encode: the scan must keep the row
        db.upsert_file(self.c, dict(rec("/m/A/alpha/1.mkv", "alpha", size=300, mtime=9.0), codec="av1",
                                    status="skipped", reason="already AV1"))
        self.assertEqual(db.get_file(self.c, "/m/A/alpha/1.mkv")["status"], "confirmed")
        self.assertEqual(db.get_file(self.c, "/m/A/alpha/1.mkv")["size"], 1000)
        a = {x["name"]: x for x in db.folders(self.c)}["alpha"]
        self.assertEqual((a["staged"], a["confirmed"]), (1, 1))
        self.assertEqual(a["current_bytes"], 300 + 1000)
        self.assertEqual(a["extra_bytes"], 1000 + 200)
        self.assertEqual(a["projected_bytes"], 500)
        self.assertEqual(db.staged_bytes(self.c), 1200)
        self.assertEqual(len(db.files_with_status(self.c, "staged")), 1)
        self.assertEqual(db.files_with_status(self.c, "confirmed", "Anime", "alpha")[0]["path"], "/m/A/alpha/1.mkv")
        self.assertEqual(db.reset_running(self.c), 0)

    def test_folders_summary(self):
        db.set_result(self.c, "/m/A/alpha/1.mkv", status="encoded", new_bytes=400, vmaf=95.5, quality=24)
        db.set_result(self.c, "/m/A/alpha/2.mkv", status="predicted", new_bytes=300, vmaf=95.1, quality=26)
        f = {x["name"]: x for x in db.folders(self.c)}
        a = f["alpha"]
        self.assertEqual(a["library"], "Anime")
        self.assertEqual(a["files"], 2)
        self.assertEqual(a["pending"], 0)
        self.assertEqual(a["encoded"], 1)
        self.assertEqual(a["predicted"], 1)
        self.assertEqual(a["original_bytes"], 2000)
        self.assertEqual(a["current_bytes"], 1400)
        self.assertEqual(a["projected_bytes"], 700)
        self.assertEqual(f["beta"]["skipped"], 1)
        self.assertEqual(f["Zeta"]["pending"], 1)
        self.assertEqual(f["Zeta"]["priority"], 0)
        self.assertFalse(f["Zeta"]["paused"])
        self.assertEqual([x["name"] for x in db.folders(self.c)], ["alpha", "beta", "Zeta"])

    def test_folder_files(self):
        files = db.folder_files(self.c, "Anime", "alpha")
        self.assertEqual(sorted(x["name"] for x in files), ["1.mkv", "2.mkv"])
        self.assertEqual(files[0]["original_bytes"], 1000)
        self.assertEqual(files[0]["codec_before"], "h264")

    def test_encode_flag(self):
        self.assertFalse({x["name"]: x for x in db.folders(self.c)}["Zeta"]["encode"])
        db.set_result(self.c, "/m/A/Zeta/1.mkv", status="predicted", new_bytes=300, quality=22, vmaf=95.2)
        n = db.set_folder(self.c, "Anime", "Zeta", encode=True)
        z = {x["name"]: x for x in db.folders(self.c)}["Zeta"]
        self.assertTrue(z["encode"])
        # an estimate is not an encode: marking the series sends its estimated files back to the queue
        self.assertEqual(db.get_file(self.c, "/m/A/Zeta/1.mkv")["status"], "pending")
        self.assertEqual(db.get_file(self.c, "/m/A/Zeta/1.mkv")["quality"], 22)  # kept: speeds up the next run
        self.assertEqual(db.next_file(self.c)["path"], "/m/A/Zeta/1.mkv")  # marked series first
        db.set_folder(self.c, "Anime", "Zeta", encode=False)
        self.assertFalse({x["name"]: x for x in db.folders(self.c)}["Zeta"]["encode"])
        self.assertTrue(db.folder_encode(self.c, "Anime", "alpha") is False)

    def test_encode_column_added_to_old_db(self):
        import sqlite3, tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE folders (library TEXT, name TEXT, priority INTEGER DEFAULT 0, paused INTEGER DEFAULT 0, PRIMARY KEY (library, name))")
        old.commit(); old.close()
        c = db.connect(path)
        db.set_folder(c, "A", "x", encode=True)
        self.assertTrue(db.folder_encode(c, "A", "x"))

    def test_state(self):
        self.assertEqual(db.get_state(self.c, "paused", "0"), "0")
        db.set_state(self.c, "paused", "1")
        self.assertEqual(db.get_state(self.c, "paused", "0"), "1")

    def test_remove_missing(self):
        n = db.remove_missing(self.c, {"/m/A/alpha/1.mkv", "/m/A/alpha/2.mkv", "/m/A/beta/1.mkv"})
        self.assertEqual(n, 1)
        self.assertIsNone(db.get_file(self.c, "/m/A/Zeta/1.mkv"))


if __name__ == "__main__":
    unittest.main()
