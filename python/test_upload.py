from email.parser import BytesParser
from email.policy import default
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

import requests

import upload


class GuardedFile:
    def __init__(self, source, allow_buffering=False):
        self.source = source
        self.read_sizes = []
        self.allow_buffering = allow_buffering

    def __getattr__(self, name):
        return getattr(self.source, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.source.close()

    def read(self, size=-1):
        if not self.allow_buffering and not 0 <= size <= 8192:
            raise AssertionError(f"Unbounded or oversized file read: {size}")
        chunk = self.source.read(size)
        self.read_sizes.append(len(chunk))
        return chunk


class UploadTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "runtime.tar.gz"
        self.payload = bytes(range(256)) * 4096
        self.path.write_bytes(self.payload)
        self.data = {"name": "1.0", "os": "linux", "arch": "x64", "channel": "stable"}
        self.headers = {"Authorization": "test-token", "Content-Type": "application/x-www-form-urlencoded"}
        self.grant = {
            "host": "https://storage.invalid/upload",
            "name": "runtime",
            "signature": "test-signature",
            "key": "test-key",
            "policy": "test-policy",
            "access_key": "test-access-key",
        }
        self.outcomes = [200]
        self.opened = []
        self.messages = []
        self.callbacks = 0
        self.allow_buffering = False
        self.enterContext(mock.patch.object(upload, "open", self.open_file, create=True))
        self.enterContext(mock.patch.object(upload.requests, "post", self.post))
        self.enterContext(mock.patch.object(upload, "log"))

    def open_file(self, filename, mode):
        self.assertEqual(Path(filename), self.path)
        self.assertEqual(mode, "rb")
        source = GuardedFile(self.path.open(mode), self.allow_buffering)
        self.opened.append(source)
        return source

    def response(self, status, data):
        return SimpleNamespace(status_code=status, text="test response", json=lambda: {"data": data})

    def post(self, url, **kwargs):
        if url == self.grant["host"]:
            source = self.opened[-1]
            prepared = requests.Request(
                "POST", url, data=kwargs.get("data"), files=kwargs.get("files"),
                headers=kwargs.get("headers"),
            ).prepare()
            self.assertNotIn("Transfer-Encoding", prepared.headers)
            self.assertNotIn("Authorization", prepared.headers)
            if self.allow_buffering:
                body = prepared.body
            else:
                connect_timeout, read_timeout = kwargs["timeout"]
                self.assertGreater(connect_timeout, 0)
                self.assertGreater(read_timeout, 0)
                self.assertIs(kwargs["allow_redirects"], False)
                self.assertEqual(source.read_sizes, [], "File was buffered while preparing the request")
                body = b"".join(iter(lambda: prepared.body.read(8192), b""))
            self.assertEqual(len(body), int(prepared.headers["Content-Length"]))
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + prepared.headers["Content-Type"].encode() + b"\r\n\r\n" + body
            )
            self.messages.append(list(message.iter_parts()))
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return self.response(outcome, {})
        self.assertEqual(kwargs["headers"]["Authorization"], "test-token")
        if url.endswith("/versions/callback"):
            self.callbacks += 1
            self.assertEqual(kwargs["data"]["key"], self.grant["key"])
            return self.response(200, {})
        self.assertTrue(url.endswith("/versions"))
        return self.response(200, self.grant)

    def run_upload(self):
        return upload.upload("Example", str(self.path), self.data, self.headers, "download.tar.gz")

    def assert_complete_upload(self, parts, download_name="download.tar.gz"):
        fields = {
            "success_action_status": "200", "name": self.grant["name"],
            "signature": self.grant["signature"], "key": self.grant["key"],
            "policy": self.grant["policy"], "OSSAccessKeyId": self.grant["access_key"],
            "Content-Disposition": f'attachment; filename="{download_name}"',
        }
        self.assertEqual(
            [part.get_param("name", header="content-disposition") for part in parts],
            [*fields, "file"],
        )
        for part in parts[:-1]:
            name = part.get_param("name", header="content-disposition")
            self.assertEqual(part.get_payload(decode=True).decode(), fields[name])
        self.assertEqual(parts[-1].get_filename(), self.path.name)
        self.assertIsNone(parts[-1]["Content-Type"])
        self.assertEqual(parts[-1].get_payload(decode=True), self.payload)
        self.assertTrue(all(source.closed for source in self.opened))
        self.assertTrue(all(len(source.read_sizes) > 1 for source in self.opened))

    def test_streams_complete_multipart_without_reading_entire_file(self):
        self.assertTrue(self.run_upload())
        self.assert_complete_upload(self.messages[0])
        self.assertEqual(self.callbacks, 1)

    def test_closes_file_and_skips_callback_on_http_failure(self):
        self.outcomes = [503]
        self.assertFalse(self.run_upload())
        self.assertTrue(self.opened[0].closed)
        self.assertEqual(self.callbacks, 0)

    def test_closes_file_and_skips_callback_on_transport_error(self):
        self.outcomes = [requests.ConnectionError("test connection failure")]
        self.assertFalse(self.run_upload())
        upload.log.assert_called_with("step 2 failed: ConnectionError")
        self.assertTrue(self.opened[0].closed)
        self.assertEqual(self.callbacks, 0)

    def test_retry_reopens_file_and_sends_complete_body(self):
        self.outcomes = [503, requests.Timeout("private storage URL"), 200]
        with mock.patch.object(upload.sys, "argv", [
            "upload.py", "Example", "1.0", "linux", "x64", "stable", "test-token", str(self.path),
        ]), mock.patch.object(upload.time, "sleep"), self.assertRaises(SystemExit) as exit_result:
            upload.main()
        self.assertEqual(exit_result.exception.code, 0)
        self.assertEqual(len(self.opened), 3)
        self.assertEqual(self.callbacks, 1)
        for parts in self.messages:
            self.assert_complete_upload(parts, "Example-1.0-linux-x64.tar.gz")
        upload.log.assert_any_call("step 2 failed: Timeout")

    def test_older_pinned_action_without_toolbelt_keeps_working(self):
        original_import = __import__

        def without_toolbelt(name, *args, **kwargs):
            if name == "requests_toolbelt":
                raise ModuleNotFoundError("Not installed by the old action", name=name)
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location("legacy_upload", upload.__file__)
        legacy_upload = importlib.util.module_from_spec(spec)
        with mock.patch("builtins.__import__", side_effect=without_toolbelt):
            spec.loader.exec_module(legacy_upload)
        self.assertIsNone(legacy_upload.MultipartEncoder)
        self.allow_buffering = True
        with mock.patch.object(legacy_upload, "open", self.open_file, create=True), mock.patch.object(legacy_upload, "log"):
            self.assertTrue(legacy_upload.upload(
                "Example", str(self.path), self.data, self.headers, "download.tar.gz",
            ))
        self.assertEqual(self.messages[0][-1].get_payload(decode=True), self.payload)
        self.assertIsNone(self.messages[0][-1]["Content-Type"])
        self.assertTrue(self.opened[0].closed)
        self.assertEqual(self.callbacks, 1)


if __name__ == "__main__":
    unittest.main()
