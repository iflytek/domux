import io
import json
import unittest
from unittest.mock import patch

from domux_runtime.ollama import OllamaClient


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body

    def __iter__(self):
        return iter(io.BytesIO(self.body))


class TestOllamaClient(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_list_models_uses_tags_endpoint(self, urlopen):
        urlopen.return_value = _Response(
            json.dumps({"models": [{"name": "domux"}]}).encode()
        )
        client = OllamaClient("http://ollama.example")

        self.assertEqual(client.list_models(), [{"name": "domux"}])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://ollama.example/api/tags")
        self.assertEqual(request.method, "GET")

    @patch("urllib.request.urlopen")
    def test_pull_streams_progress(self, urlopen):
        urlopen.return_value = _Response(
            b'{"status":"pulling","completed":1,"total":2}\n{"status":"success"}\n'
        )
        updates = []
        result = OllamaClient().pull("gemma3", progress=updates.append)

        self.assertEqual(result, {"status": "success"})
        self.assertEqual(len(updates), 2)

    @patch("urllib.request.urlopen")
    def test_load_sends_empty_generate_request(self, urlopen):
        urlopen.return_value = _Response(b'{"done":true}')
        OllamaClient().load("domux", keep_alive="-1m")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        self.assertEqual(
            payload,
            {
                "model": "domux",
                "prompt": "",
                "stream": False,
                "keep_alive": "-1m",
            },
        )


if __name__ == "__main__":
    unittest.main()
