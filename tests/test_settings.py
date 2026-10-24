"""Tests for app/rx_settings.py: settings changed on the page, stored in the database, applied to cfg."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_config  # noqa: E402
import rx_db  # noqa: E402
import rx_settings as st  # noqa: E402


class Settings(unittest.TestCase):
    def setUp(self):
        self.conn = rx_db.connect(":memory:")
        self.cfg = rx_config.config_from_env({"RX_ROOTS": "/m/A", "RX_MODE": "manual"})

    def test_current_values_come_from_cfg(self):
        v = st.current(self.cfg)
        self.assertEqual(v["mode"], "manual")
        self.assertEqual(v["parallel"], 2)
        self.assertEqual(v["rescan_s"], 86400)
        self.assertTrue(v["check_every_episode"])
        self.assertEqual(v["target_vmaf"], 95.0)
        self.assertEqual(v["max_ratio"], 0.95)
        self.assertEqual(set(v), set(st.FIELDS))

    def test_save_validates_stores_and_applies(self):
        v = st.save(self.conn, self.cfg, {"mode": "stage", "parallel": 3, "rescan_s": 86400,
                                          "check_every_episode": False, "target_vmaf": 94.5, "max_ratio": 0.7})
        self.assertEqual(v["mode"], "stage")
        self.assertEqual(self.cfg["mode"], "stage")          # the running service sees it at once
        self.assertFalse(self.cfg["check_every_episode"])
        self.assertEqual(rx_db.get_state(self.conn, "parallel", "x"), "3")  # the pool reads parallel from here
        # a fresh cfg (after a restart) gets the stored values back
        cfg2 = rx_config.config_from_env({"RX_ROOTS": "/m/A", "RX_MODE": "report"})
        st.load(self.conn, cfg2)
        self.assertEqual(cfg2["mode"], "stage")
        self.assertEqual(cfg2["rescan_s"], 86400)
        self.assertEqual(cfg2["target_vmaf"], 94.5)
        self.assertEqual(cfg2["parallel"], 3)

    def test_rejects_bad_values(self):
        for bad in ({"mode": "delete"}, {"parallel": 9}, {"rescan_s": 30}, {"target_vmaf": 120},
                    {"max_ratio": 1.5}, {"check_every_episode": "maybe"}, {"unknown": 1}):
            with self.assertRaises(ValueError, msg=str(bad)):
                st.save(self.conn, self.cfg, bad)
        self.assertEqual(self.cfg["mode"], "manual")  # nothing applied after an error

    def test_partial_save_keeps_the_rest(self):
        st.save(self.conn, self.cfg, {"target_vmaf": 96})
        self.assertEqual(st.current(self.cfg)["mode"], "manual")
        self.assertEqual(self.cfg["target_vmaf"], 96.0)


if __name__ == "__main__":
    unittest.main()
