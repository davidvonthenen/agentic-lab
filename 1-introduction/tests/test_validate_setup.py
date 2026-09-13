"""Offline unit tests; no container engine, model keys, or model downloads required."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import validate_setup as v


class HealthTests(unittest.TestCase):
    def test_cluster_accepts_green_and_yellow_not_red(self):
        self.assertTrue(v.cluster_ready({"status": "green"}))
        self.assertTrue(v.cluster_ready({"status": "yellow"}))
        self.assertFalse(v.cluster_ready({"status": "red"}))
        self.assertFalse(v.cluster_ready({"status": "green", "timed_out": True}))

    def test_dashboards_current_and_legacy_schema(self):
        self.assertTrue(v.dashboards_ready({"status": {"overall": {"level": "available"}}}))
        self.assertTrue(v.dashboards_ready({"status": {"overall": {"state": "green"}}}))
        self.assertFalse(v.dashboards_ready({"status": {"overall": {"level": "unavailable"}}}))

    def test_bad_model_urls_rejected(self):
        for url in ("localhost:8000", "http://example.com/v1", "https://secret@example.com/v1", "https://example.com/v1?key=secret", "https://example.com/v1/chat/completions"):
            with self.subTest(url=url), self.assertRaises(v.CheckError):
                v.validate_base_url(url)
        self.assertEqual(v.validate_base_url("https://example.com/v1/"), "https://example.com/v1")

    def test_secret_redaction(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "a-private-example-key"}):
            cleaned = v.redact("a-private-example-key Bearer token-value sk-other-secret")
        self.assertNotIn("a-private-example-key", cleaned)
        self.assertNotIn("token-value", cleaned)
        self.assertNotIn("sk-other-secret", cleaned)

    def test_token_ceiling_is_not_redacted(self):
        with patch.dict(os.environ, {"EXTERNAL_ORCH_MAX_TOKENS": "131072", "HF_TOKEN": "private-hf-token"}, clear=True):
            self.assertEqual(v.redact("Limit 131072; private-hf-token"), "Limit 131072; [REDACTED]")

    def test_provider_selection_and_placeholders(self):
        with patch.dict(os.environ, {"USE_EXTERNAL_OPENAI": "true", "OPENAI_API_KEY": "<YOUR_KEY>"}, clear=True):
            with self.assertRaises(v.CheckError):
                v.provider_key("orch")
        with patch.dict(os.environ, {"USE_EXTERNAL_OPENAI": "false", "EXTERNAL_ORCH_API_KEY": "orch-key", "EXTERNAL_LLM_API_KEY": "llm-key"}, clear=True):
            self.assertEqual(v.provider_key("orch"), "orch-key")
            self.assertEqual(v.provider_key("llm"), "llm-key")

    def test_exit_codes(self):
        runner = v.Validator()
        self.assertEqual(runner.exit_code(), 0)
        runner.incomplete = True
        self.assertEqual(runner.exit_code(), 2)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.record("test", "FAIL", "bad")
        self.assertEqual(runner.exit_code(), 1)

    def test_wait_retries_until_ready(self):
        with patch.object(v, "json_request", side_effect=[v.CheckError("starting"), {"status": "green"}]), patch.object(v.time, "sleep"):
            self.assertEqual(v.wait_json("http://test", v.cluster_ready, seconds=3)["status"], "green")


class VectorTests(unittest.TestCase):
    def test_vector_roundtrip_and_cleanup(self):
        calls = []
        def fake(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/_search"):
                return {"hits": {"hits": [{"_id": "probe"}]}}
            return {"acknowledged": True}
        with patch.object(v, "json_request", side_effect=fake):
            v.check_vector_roundtrip("http://test")
        self.assertEqual([call[1]["method"] for call in calls], ["PUT", "PUT", "POST", "DELETE"])
        self.assertIn("lab-validation-", calls[0][0])
        self.assertEqual(calls[0][1]["payload"]["mappings"]["properties"]["vector"]["method"]["engine"], "lucene")

    def test_vector_failure_still_deletes_index(self):
        calls = []
        def fake(url, **kwargs):
            calls.append(kwargs["method"])
            if url.endswith("/_search"):
                raise v.CheckError("query failed")
            return {}
        with patch.object(v, "json_request", side_effect=fake), self.assertRaises(v.CheckError):
            v.check_vector_roundtrip("http://test")
        self.assertEqual(calls[-1], "DELETE")


class ModelTests(unittest.TestCase):
    def fake_module(self, text="READY", finish="stop"):
        self.calls = []
        calls = self.calls
        class Client:
            def __init__(self, **kwargs):
                self.chat = self
                self.completions = self
                self.initializer = kwargs
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def create(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content=text), finish_reason=finish)])
        return types.SimpleNamespace(OpenAI=Client)

    def test_preserves_legacy_parameters_and_bounds_tokens(self):
        params = {"model": "test-model", "temperature": 0.0, "top_p": 0.9, "max_tokens": 131072}
        with patch.dict(sys.modules, {"openai": self.fake_module()}):
            v.model_probe("https://example.com/v1", "test-key", params, tokens=512, full_limits=False, timeout=1)
        self.assertEqual(self.calls[0]["max_tokens"], 512)
        self.assertEqual(self.calls[0]["temperature"], 0.0)
        self.assertEqual(self.calls[0]["top_p"], 0.9)
        self.assertNotIn("max_completion_tokens", self.calls[0])
        self.assertEqual(params["max_tokens"], 131072)

    def test_full_limits_are_opt_in(self):
        with patch.dict(sys.modules, {"openai": self.fake_module()}):
            v.model_probe("https://example.com/v1", "test-key", {"model": "test", "max_tokens": 131072}, tokens=512, full_limits=True, timeout=1)
        self.assertEqual(self.calls[0]["max_tokens"], 131072)

    def test_respects_modern_parameters_when_source_uses_them(self):
        with patch.dict(sys.modules, {"openai": self.fake_module()}):
            v.model_probe("https://example.com/v1", "test-key", {"model": "test", "max_completion_tokens": 4096}, tokens=512, full_limits=False, timeout=1)
        self.assertEqual(self.calls[0]["max_completion_tokens"], 512)
        self.assertNotIn("max_tokens", self.calls[0])

    def test_empty_final_answer_fails(self):
        with patch.dict(sys.modules, {"openai": self.fake_module(text=None)}), self.assertRaises(v.CheckError):
            v.model_probe("https://example.com/v1", "test-key", {"model": "test"}, tokens=512, full_limits=False, timeout=1)

    def test_truncated_answer_fails(self):
        with patch.dict(sys.modules, {"openai": self.fake_module(finish="length")}), self.assertRaises(v.CheckError):
            v.model_probe("https://example.com/v1", "test-key", {"model": "test"}, tokens=512, full_limits=False, timeout=1)

    def test_rejected_parameter_not_silently_retried(self):
        class Client:
            def __init__(self, **kwargs): self.chat = self; self.completions = self
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def create(self, **kwargs): raise v.CheckError("Unsupported parameter: max_tokens")
        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=Client)}), self.assertRaisesRegex(v.CheckError, "Unsupported parameter"):
            v.model_probe("https://example.com/v1", "test-key", {"model": "test", "max_tokens": 4096}, tokens=512, full_limits=False, timeout=1)


class RuntimeHandlerTests(unittest.TestCase):
    def test_runtime_health_and_no_file_serving(self):
        import tempfile
        from runtime_health import Handler
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for component in v.COMPONENTS:
                (root / component).mkdir()
                (root / component / "Makefile").touch()
            with patch.dict(os.environ, {"LAB_REPO_DIR": directory}):
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    data = v.json_request(base + "/healthz")
                    self.assertTrue(data["repository_ready"])
                    self.assertEqual(data["service"], "agentic-lab")
                    with self.assertRaisesRegex(v.CheckError, "HTTP 404"):
                        v.json_request(base + "/.env")
                    (root / "news_agent" / "Makefile").unlink()
                    with self.assertRaisesRegex(v.CheckError, "HTTP 503"):
                        v.json_request(base + "/healthz")
                finally:
                    server.shutdown(); server.server_close(); thread.join()


class HTTPTests(unittest.TestCase):
    def test_real_local_http_json_and_error_handling(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ok":
                    self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"green"}')
                else:
                    self.send_response(503); self.end_headers(); self.wfile.write(b'{"error":"starting"}')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(v.json_request(base + "/ok"), {"status": "green"})
            with self.assertRaisesRegex(v.CheckError, "HTTP 503"):
                v.json_request(base + "/fail")
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
