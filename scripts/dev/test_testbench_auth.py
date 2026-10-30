import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("testbench_auth", Path(__file__).with_name("testbench_auth.py"))
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class TestTestbenchAuth(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = Path(self.temp.name) / "env"
        self.env.write_text('TOKEN="mul_original"\n')
        self.cache = Path(self.temp.name) / "cache"

    def test_mints_for_existing_account_and_reuses_token(self):
        minted = []
        def request(base, path, token, body=None):
            self.assertEqual(base, "http://127.0.0.1:12345")
            if path == "/api/me": return {"id": "user"}
            if path == "/api/workspaces": return [{"id": "workspace"}]
            minted.append(token)
            return {"token": "mul_testbench"}
        with patch.object(auth, "request", request):
            self.assertEqual(auth.main("12345", self.env, "TOKEN", self.cache), "mul_testbench")
            self.assertEqual(auth.main("12345", self.env, "TOKEN", self.cache), "mul_testbench")
        self.assertEqual(minted, ["mul_original"])
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_refuses_empty_workspace(self):
        with patch.object(auth, "request", side_effect=[{"id": "user"}, []]):
            with self.assertRaises(ValueError):
                auth.main("12345", self.env, "TOKEN", self.cache)
        self.assertFalse(self.cache.exists())

    def test_replaces_cached_token_for_another_user(self):
        self.cache.write_text(json.dumps({"token": "mul_other"}))
        def request(base, path, token, body=None):
            if path == "/api/me": return {"id": "other" if token == "mul_other" else "user"}
            if path == "/api/workspaces": return [{"id": "workspace"}]
            return {"token": "mul_new"}
        with patch.object(auth, "request", request):
            self.assertEqual(auth.main("12345", self.env, "TOKEN", self.cache), "mul_new")


if __name__ == "__main__":
    unittest.main()
