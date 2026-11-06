import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import notify


class NotifyTests(unittest.TestCase):
    def test_posts_json_content_with_user_agent_and_limit(self):
        with patch("notify.urllib.request.urlopen") as urlopen:
            self.assertTrue(notify.send({"discord_webhook_url": "https://discord.test/hook"}, "x" * 2500))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("User-agent"), "pp8-worker")
        self.assertEqual(len(json.loads(request.data)["content"]), 2000)
        self.assertEqual(json.loads(request.data)["allowed_mentions"], {"parse": []})

    def test_error_log_hides_webhook_url(self):
        url = "https://discord.test/hook/secret-token"
        error = urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        with patch("notify.urllib.request.urlopen", side_effect=error), patch("sys.stderr", new=io.StringIO()) as err:
            self.assertFalse(notify.send({"discord_webhook_url": url}, "hi"))
        self.assertIn("HTTPError 404", err.getvalue())
        self.assertNotIn("secret-token", err.getvalue())


if __name__ == "__main__":
    unittest.main()
