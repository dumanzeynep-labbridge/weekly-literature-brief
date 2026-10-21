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

    @patch("literature_brief.http.time.sleep")
    @patch("literature_brief.http.urlopen")
    def test_exhausted_credit_stops_without_retry_or_sensitive_body(self, open_url, sleep):
        body = b'{"error":{"type":"insufficient_quota","code":"credit_balance_exhausted","message":"secret-key private-account"}}'
        open_url.side_effect = HTTPError("https://api.openai.com/v1/responses", 429, "", {}, BytesIO(body))
        with self.assertRaises(HttpError) as result:
            HttpClient().get_json("https://api.openai.com/v1/responses", headers={"Authorization": "secret-key"})
        error = result.exception
        self.assertEqual(error.status, 429)
        self.assertEqual(error.error_code, "credit_balance_exhausted")
        self.assertTrue(error.permanent)
        self.assertIn("credit_balance_exhausted", str(error))
        self.assertNotIn("secret-key", str(error))
        self.assertNotIn("private-account", str(error))
        self.assertEqual(open_url.call_count, 1)
        sleep.assert_not_called()

    @patch("literature_brief.http.time.sleep")
    @patch("literature_brief.http.urlopen")
    def test_temporary_provider_rate_limit_still_retries(self, open_url, sleep):
        body = b'{"error":{"type":"rate_limit_error","code":"rate_limit_exceeded","message":"private text"}}'
        open_url.side_effect = [HTTPError("https://api.openai.com", 429, "", {}, BytesIO(body)), Response(b'{"ok":true}')]
        self.assertTrue(HttpClient().get_json("https://api.openai.com/v1/responses")["ok"])
        self.assertEqual(open_url.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    @patch("literature_brief.http.urlopen")
    def test_unknown_error_codes_and_non_json_never_leak(self, open_url):
        for body in (b'{"error":{"code":"secret-key","message":"private text"}}', b'private non-JSON text'):
            with self.subTest(body=body):
                open_url.side_effect = HTTPError("https://api.openai.com", 429, "", {}, BytesIO(body))
                with self.assertRaises(HttpError) as result:
                    HttpClient(retries=0).get_json("https://api.openai.com/v1/responses")
                self.assertIsNone(result.exception.error_code)
                self.assertFalse(result.exception.permanent)
                self.assertNotIn("private", str(result.exception))
                self.assertNotIn("secret", str(result.exception))

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
