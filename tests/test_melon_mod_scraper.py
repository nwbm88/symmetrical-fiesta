import unittest

import melon_mod_scraper as scraper


class ScraperHelpersTest(unittest.TestCase):
    def test_slugify_removes_unsafe_characters(self):
        self.assertEqual(scraper.slugify(" cool<>weapon?? pack.zip "), "cool weapon pack.zip")

    def test_category_for_uses_keywords(self):
        self.assertEqual(scraper.category_for("https://mods.test/cars/tank-pack.zip"), "vehicles")
        self.assertEqual(scraper.category_for("https://mods.test/random-file.zip"), "uncategorized")

    def test_extension_filters(self):
        self.assertTrue(scraper.has_allowed_extension("https://mods.test/file.melmod", (".melmod",)))
        self.assertFalse(scraper.has_allowed_extension("https://mods.test/file.apk", (".melmod",)))
        self.assertTrue(scraper.has_blocked_extension("https://mods.test/file.xapk"))

    def test_same_domain(self):
        self.assertTrue(scraper.same_domain("https://mods.test/page", {"mods.test"}))
        self.assertFalse(scraper.same_domain("https://other.test/page", {"mods.test"}))


if __name__ == "__main__":
    unittest.main()
