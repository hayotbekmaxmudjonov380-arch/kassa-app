"""updater.py va ma'lumotlar joylashuvi testlari (tarmoqsiz)."""

import io
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import updater


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def release_payload(tag="v9.9.9", with_exe=True):
    assets = []
    if with_exe:
        assets.append(
            {
                "name": "GOLD_Kassa.exe",
                "browser_download_url": "https://example.test/GOLD_Kassa.exe",
            }
        )
    return {
        "tag_name": tag,
        "body": "Yangi versiya",
        "assets": assets,
    }


class VersionTest(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2"), (1, 2, 0))
        self.assertEqual(updater.parse_version("buzilgan"), ())

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("v1.1.0", "1.0.0"))
        self.assertFalse(updater.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("v0.9.9", "1.0.0"))
        self.assertFalse(updater.is_newer("", "1.0.0"))


class CheckUpdateTest(unittest.TestCase):
    def _opener(self, payload, calls=None):
        def opener(request, timeout=10):
            if calls is not None:
                calls.append(request.full_url)
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        return opener

    def test_newer_release_with_exe_is_detected(self):
        info = updater.check_for_update(
            current="1.0.0", opener=self._opener(release_payload())
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "9.9.9")
        self.assertEqual(info.tag, "v9.9.9")
        self.assertTrue(info.url.endswith("GOLD_Kassa.exe"))

    def test_same_version_is_ignored(self):
        info = updater.check_for_update(
            current="9.9.9", opener=self._opener(release_payload("v9.9.9"))
        )
        self.assertIsNone(info)

    def test_release_without_exe_is_ignored(self):
        info = updater.check_for_update(
            current="1.0.0",
            opener=self._opener(release_payload(with_exe=False)),
        )
        self.assertIsNone(info)

    def test_offline_is_silent(self):
        def opener(request, timeout=10):
            raise ConnectionError("internet yo'q")

        self.assertIsNone(updater.check_for_update(opener=opener))

    def test_broken_json_is_silent(self):
        def opener(request, timeout=10):
            return FakeResponse(b"{ buzilgan")

        self.assertIsNone(updater.check_for_update(opener=opener))

    def test_api_url_can_be_overridden_by_env(self):
        calls = []
        payload = release_payload()
        with mock.patch.dict(
            os.environ, {"KASSA_UPDATE_API": "https://example.test/release"}
        ):
            updater.check_for_update(
                current="1.0.0",
                opener=self._opener(payload, calls),
            )
        self.assertEqual(calls, ["https://example.test/release"])


class DownloadTest(unittest.TestCase):
    def test_download_writes_exe_to_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"KASSA_UPDATE_DIR": tmp}):

                def opener(request, timeout=10):
                    return FakeResponse(b"MZ-test")

                info = updater.UpdateInfo(
                    version="9.9.9",
                    tag="v9.9.9",
                    url="https://example.test/GOLD_Kassa.exe",
                )
                path = updater.download_update(info, opener=opener)
                self.assertEqual(os.path.dirname(path), tmp)
                with open(path, "rb") as fh:
                    self.assertEqual(fh.read(), b"MZ-test")

    def test_script_waits_backs_up_db_and_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"KASSA_UPDATE_DIR": tmp}):
                script = updater.write_update_script(
                    r"C:\Temp\new.exe",
                    r"C:\Apps\GOLD_Kassa.exe",
                    db_path=r"C:\Data\kassa.db",
                    pid=4242,
                )
                with open(script, "r", encoding="utf-8", newline="") as fh:
                    text = fh.read()
        self.assertIn("4242", text)
        self.assertIn(r"C:\Temp\new.exe", text)
        self.assertIn(r"C:\Apps\GOLD_Kassa.exe", text)
        self.assertIn("copy /Y", text)
        self.assertIn("%DB%.preupdate", text)
        self.assertIn("start \"\"", text)
        self.assertIn("goto wait", text)


class DataLocationTest(unittest.TestCase):
    def test_data_lives_in_appdata_not_next_to_exe(self):
        if os.environ.get("KASSA_DATA_DIR"):
            self.skipTest("test papkasi almashtirilgan")
        roaming = os.environ.get("APPDATA")
        self.assertTrue(roaming)
        self.assertEqual(
            os.path.abspath(config.DATA_DIR),
            os.path.abspath(os.path.join(roaming, "GoldKassa")),
        )
        self.assertEqual(os.path.dirname(config.DB_PATH), config.DATA_DIR)

    def test_migration_copies_missing_files_without_overwriting(self):
        with tempfile.TemporaryDirectory() as old, tempfile.TemporaryDirectory() as new:
            with open(os.path.join(old, "settings.json"), "w", encoding="utf-8") as fh:
                fh.write('{"last_kassir": "Ali"}')
            with open(os.path.join(old, "kassa.db"), "wb") as fh:
                fh.write(b"db")
            with mock.patch.object(config, "LEGACY_DIRS", [old]):
                moved = config.migrate_legacy_files(new)
                self.assertEqual(len(moved), 2)
                self.assertTrue(os.path.exists(os.path.join(new, "kassa.db")))
                # Ikkinchi marta hech narsa ustiga yozilmaydi.
                with open(os.path.join(new, "kassa.db"), "wb") as fh:
                    fh.write(b"yangi")
                config.migrate_legacy_files(new)
                with open(os.path.join(new, "kassa.db"), "rb") as fh:
                    self.assertEqual(fh.read(), b"yangi")


if __name__ == "__main__":
    unittest.main()
