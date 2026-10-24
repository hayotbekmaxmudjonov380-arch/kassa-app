"""settings.py uchun testlar."""

import json
import os
import tempfile
import unittest
from unittest import mock

import settings as settings_module
from settings import AppSettings


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_file_missing(self):
        cfg = AppSettings(self.path)
        self.assertTrue(cfg.spreadsheet_id)
        self.assertEqual(cfg.worksheet_name, "Kassa jurnali")
        self.assertEqual(cfg.last_kassir, "")

    def test_save_and_reload(self):
        cfg = AppSettings(self.path)
        cfg.set("last_kassir", "Alisher")
        cfg.set("worksheet_name", "Boshqa varaq")
        self.assertTrue(cfg.save())

        reloaded = AppSettings(self.path)
        self.assertEqual(reloaded.last_kassir, "Alisher")
        self.assertEqual(reloaded.worksheet_name, "Boshqa varaq")

    def test_broken_json_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ buzilgan json")
        cfg = AppSettings(self.path)
        self.assertEqual(cfg.worksheet_name, "Kassa jurnali")

    def test_unknown_keys_are_ignored(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"last_kassir": "Dilnoza", "hacker": "x"}, fh)
        cfg = AppSettings(self.path)
        self.assertEqual(cfg.last_kassir, "Dilnoza")
        self.assertIsNone(cfg.get("hacker"))

    def test_credentials_are_copied_into_app_folder(self):
        source = os.path.join(self.tmp.name, "kalit.json")
        with open(source, "w", encoding="utf-8") as fh:
            json.dump({"type": "service_account"}, fh)

        target = os.path.join(self.tmp.name, "service_account.json")
        cfg = AppSettings(self.path)
        with mock.patch.object(settings_module, "SERVICE_ACCOUNT_FILE", target):
            result = cfg.set_credentials_from(source)

        self.assertEqual(result, target)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(cfg.credentials_file, target)

    def test_stale_credentials_path_falls_back_to_app_folder(self):
        """Boshqa kompyuterdan kelgan eski yo'l mavjud emasligi aniqlansa,
        dastur yonidagi service_account.json ga qaytadi (papkaga tashlash kifoya)."""
        target = os.path.join(self.tmp.name, "service_account.json")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump({}, fh)

        cfg = AppSettings(self.path)
        cfg.set("credentials_file", r"C:\Users\boshqa_pc\OneDrive\Desktop\kalit.json")
        with mock.patch.object(settings_module, "SERVICE_ACCOUNT_FILE", target):
            self.assertEqual(cfg.credentials_file, target)
            # Eski yo'l haqiqatan mavjud bo'lsa — o'sha saqlanadi.
            real = os.path.abspath(__file__)
            cfg.set("credentials_file", real)
            self.assertEqual(cfg.credentials_file, real)


if __name__ == "__main__":
    unittest.main()
