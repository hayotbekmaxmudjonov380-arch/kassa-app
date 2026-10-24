"""Web app handler va sahifa uchun testlar."""

import os
import unittest
from io import BytesIO

import web_app


class DummyRequest:
    def __init__(self, body=b""):
        self.body = body

    def makefile(self, *args, **kwargs):
        return BytesIO(self.body)


class DummyServer:
    def __init__(self):
        pass


class WebAppTest(unittest.TestCase):
    def test_import_web_app(self):
        self.assertEqual(web_app.PORT, 8000)

    def test_page_has_server_rendered_date(self):
        page = web_app.render_page("23.10.2026")
        self.assertIn("23.10.2026", page)
        self.assertNotIn("{{TODAY}}", page)
        self.assertNotIn("--.--.----", page)

    def test_date_is_not_editable(self):
        page = web_app.render_page("23.10.2026")
        self.assertIn('id="dateValue"', page)
        self.assertNotIn('id="sanaInput"', page)
        self.assertNotIn('type="date"', page)

    def test_journal_has_all_nine_columns(self):
        page = web_app.render_page("23.10.2026")
        headers = [
            "KIRIM — Naqd",
            "KIRIM — Click",
            "KIRIM — Terminal",
            "KIRIM — Comment",
            "CHIQIM — Naqd",
            "CHIQIM — Click",
            "CHIQIM — Comment",
        ]
        for header in headers:
            self.assertIn(header, page)
        self.assertIn("Kunni yopish", page)
        self.assertNotIn("Yangi qator qo'shish", page)

    def test_template_file_exists(self):
        self.assertTrue(os.path.isfile(web_app.TEMPLATE_PATH))

    def test_page_shows_logo(self):
        page = web_app.render_page("23.10.2026")
        self.assertIn('class="brand-logo"', page)
        self.assertIn('src="/image/logotip.jpg"', page)
        self.assertTrue(os.path.isfile(web_app.LOGO_PATH))

    def test_template_preallocates_32_ready_rows(self):
        """Jurnal oldindan 32 ta kiritish qatoriga ega — kassir katak
        qo'shish bilan vaqt ketkazmaydi."""
        with open(web_app.TEMPLATE_PATH, encoding="utf-8") as fh:
            template = fh.read()
        self.assertIn("const READY_ROWS = 32;", template)
        self.assertIn('querySelectorAll("#journalBody tr.entry")', template)
        self.assertIn("buildEntryRow", template)
        # Eski bitta-qatorli kiritish mantiqi o'chirilgan bo'lishi kerak
        self.assertNotIn('id="in_kn"', template)
        self.assertNotIn("AMOUNT_FIELDS", template)

    def test_template_has_editable_time_and_readable_amounts(self):
        """Vaqt kataki erkin tahrirlanadi; uzun matn boshi ko'rinsin deb
        summalar yozayotganda chapdan hizalanadi."""
        with open(web_app.TEMPLATE_PATH, encoding="utf-8") as fh:
            template = fh.read()
        self.assertIn('data-field="vt"', template)
        self.assertIn(".cell-input.time-input", template)
        self.assertIn(".cell-input.amount:focus", template)
        self.assertIn("text-align: left", template)

    # --------------------------------------------------------- vaqt tekshiruvi
    def test_valid_time_accepts_normal_values(self):
        for value in ("18:45", "9:05", "00:00", "23:59", "18:45:00", " 18:45 "):
            self.assertTrue(web_app.valid_time(value), value)

    def test_valid_time_rejects_bad_values(self):
        for value in ("", "25:00", "18:60", "18-45", "abc", "18:4", "18:45:99"):
            self.assertFalse(web_app.valid_time(value), value)

    # ------------------------------------------------------- CSRF himoyasi
    def test_cross_site_origin_is_blocked(self):
        headers = {"Origin": "http://evil.example"}
        self.assertTrue(web_app.cross_site_blocked(headers))

    def test_localhost_origin_is_allowed(self):
        for origin in ("http://localhost:8000", "http://127.0.0.1:8000"):
            self.assertFalse(web_app.cross_site_blocked({"Origin": origin}))

    def test_no_origin_from_non_browser_clients_is_allowed(self):
        self.assertFalse(web_app.cross_site_blocked({}))

    def test_sec_fetch_site_cross_site_is_blocked(self):
        headers = {"Sec-Fetch-Site": "cross-site"}
        self.assertTrue(web_app.cross_site_blocked(headers))

    def test_import_has_no_side_effects(self):
        """Import paytida haqiqiy baza yaratilmaydi (test xavfsizligi)."""
        self.assertIsNone(web_app._context)


if __name__ == "__main__":
    unittest.main()
