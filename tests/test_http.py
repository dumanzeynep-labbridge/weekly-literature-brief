from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from literature_brief.http import HttpClient, HttpError


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class HttpTests(unittest.TestCase):
    @patch("literature_brief.http.time.sleep")
    @patch("literature_brief.http.urlopen")
    def test_transient_rate_limit_retried_and_parameters_encoded(self, open_url, sleep):
        open_url.side_effect = [
            HTTPError("https://api.example.com", 429, "", {"Retry-After": "2"}, None),
            Response(b'{"ok":true}'),
        ]
        result = HttpClient(retries=1).get_json("https://api.example.com/search", {"query": "CHO & cells"})
        self.assertTrue(result["ok"])
        self.assertIn("query=CHO+%26+cells", open_url.call_args.args[0].full_url)
        self.assertEqual(sleep.call_count, 1)

    @patch("literature_brief.http.urlopen")
    def test_auth_error_is_not_retried_or_leaked(self, open_url):
        open_url.side_effect = HTTPError("https://api.example.com", 401, "secret body", {}, None)
        with self.assertRaises(HttpError) as result:
            HttpClient().get_json("https://api.example.com", headers={"Authorization": "secret-key"})
        self.assertEqual(result.exception.status, 401)
        self.assertNotIn("secret", str(result.exception))
        self.assertEqual(open_url.call_count, 1)

    @patch("literature_brief.http.urlopen")
    def test_retry_exhaustion_is_bounded(self, open_url):
        open_url.side_effect = HTTPError("https://api.example.com", 503, "", {}, None)
        with patch("literature_brief.http.time.sleep"), self.assertRaises(HttpError):
            HttpClient(retries=2).get_json("https://api.example.com")
        self.assertEqual(open_url.call_count, 3)

    @patch("literature_brief.http.urlopen")
    def test_oversized_response_and_invalid_json_rejected(self, open_url):
        open_url.return_value = Response(b'{"too":"large"}')
        with self.assertRaises(HttpError):
            HttpClient(max_bytes=5).get_json("https://api.example.com")
        open_url.return_value = Response(b'not json')
        with self.assertRaises(HttpError):
            HttpClient().get_json("https://api.example.com")


if __name__ == "__main__":
    unittest.main()
