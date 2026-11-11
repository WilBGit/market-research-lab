import unittest

from scripts.scan_once import split_discord


class DiscordDeliveryTests(unittest.TestCase):
    def test_alert_chunks_stay_within_discord_message_limit(self):
        content = "NQ/MNQ context\n\n" + ("level and trend | " * 180)

        chunks = split_discord(content, max_len=1900)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1900 for chunk in chunks))
        self.assertEqual("".join(chunks), content.replace("\n\n", ""))


if __name__ == "__main__":
    unittest.main()
