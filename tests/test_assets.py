import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kit import assets as A


class Response(io.BytesIO):
    def geturl(self):
        return "https://firmware.example/stock.ipsw"


class FirmwareDownloadTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.data = b"verified firmware fixture"
        self.record = {"version": "3.1.3", "filename": "stock.ipsw", "size": len(self.data),
                       "sha256": hashlib.sha256(self.data).hexdigest(),
                       "url": "https://firmware.example/stock.ipsw"}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(A.platforms, "data_dir", return_value=self.root))
        self.stack.enter_context(patch.object(A.firmware, "firmwares", return_value=[self.record]))
        self.urlopen = self.stack.enter_context(patch.object(A.urllib.request, "urlopen", return_value=Response(self.data)))

    def test_download_verifies_then_atomically_publishes(self):
        progress = []
        result = A.fetch_firmware("3.1.3", lambda *args: progress.append(args))
        self.assertEqual(Path(result["path"]).read_bytes(), self.data)
        self.assertTrue(result["verified"])
        self.assertEqual(progress[-1][1], len(self.data))
        self.assertFalse(list((self.root / "firmware").glob("*.part")))

    def test_valid_cached_file_needs_no_network(self):
        target = self.root / "firmware/stock.ipsw"
        target.parent.mkdir()
        target.write_bytes(self.data)
        A.fetch_firmware("3.1.3")
        self.urlopen.assert_not_called()

    def test_checksum_or_size_failure_never_publishes(self):
        self.urlopen.return_value = Response(b"wrong download")
        with self.assertRaises(A.AssetError):
            A.fetch_firmware("3.1.3")
        self.assertFalse((self.root / "firmware/stock.ipsw").exists())
        self.assertFalse(list((self.root / "firmware").glob("*.part")))

    def test_oversized_reply_is_rejected(self):
        self.urlopen.return_value = Response(self.data + b"extra")
        with self.assertRaisesRegex(A.AssetError, "exceeds"):
            A.fetch_firmware("3.1.3")

    def test_interrupted_download_removes_partial_file(self):
        self.urlopen.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            A.fetch_firmware("3.1.3")
        self.assertFalse(list((self.root / "firmware").glob("*.part")))

    def test_insecure_url_cannot_start_download(self):
        self.record["url"] = "http://firmware.example/stock.ipsw"
        with self.assertRaises(A.AssetError):
            A.fetch_firmware("3.1.3")
        self.urlopen.assert_not_called()

