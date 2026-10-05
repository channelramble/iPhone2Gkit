import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from kit import assets, cli, legacy_assets as L, ramdisk


class Response(io.BytesIO):
    def geturl(self):
        return "https://github.com/example/setup.zip"


class SetupDownloadTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.converter = b"converter fixture"
        base = io.BytesIO()
        with zipfile.ZipFile(base, "w") as z:
            z.writestr("bin/plutil", self.converter)
        self.files = {"ios1-apps/catalog.json": b"[]",
                      "iLiberty-portable/iLiberty/BasePack.zip": base.getvalue(),
                      "iLiberty-portable/iLiberty/iLibertyRD.zip": b"ramdisk fixture"}
        self.blob = self.zip(self.files)
        self.record = {"schema": 1, "version": "1", "filename": "setup.zip",
                       "url": "https://github.com/example/setup.zip",
                       "size": len(self.blob), "sha256": hashlib.sha256(self.blob).hexdigest(),
                       "uncompressed_size": sum(map(len, self.files.values())),
                       "files": {n: {"size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                                 for n, d in self.files.items()}}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(L, "manifest", return_value=self.record))
        self.stack.enter_context(patch.object(L.platforms, "data_dir", return_value=self.root / "data"))
        self.stack.enter_context(patch.object(L.platforms, "cache_dir", return_value=self.root / "cache"))
        self.stack.enter_context(patch.object(ramdisk, "PLUTIL_SHA", hashlib.sha256(self.converter).hexdigest()))
        self.network = self.stack.enter_context(patch.object(L.urllib.request, "urlopen", return_value=Response(self.blob)))

    def zip(self, files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in files.items():
                archive.writestr(L.ARCHIVE_ROOT + name, data)
        return stream.getvalue()

    def test_verified_download_installs_converter_and_auto_detects_kit(self):
        result = L.fetch_kit()
        self.assertTrue(result["verified"])
        self.assertEqual(Path(result["kit"]).joinpath("ios1-apps/catalog.json").read_bytes(), b"[]")
        self.assertEqual((self.root / "data/resources/plutil-ios1").read_bytes(), self.converter)
        # CI also tests a real downloaded kit via IOS1KIT_ASSETS. Automatic
        # discovery here must inspect this test's isolated managed directory,
        # without the intentional higher-priority override from that fixture.
        with patch.dict(os.environ, {"IOS1KIT_ASSETS": ""}):
            self.assertEqual(cli.find_kit(), result["kit"])
        self.assertFalse((Path(result["kit"]) / ramdisk.ASSETS_KC).exists())

    def test_second_setup_uses_installed_bundle_without_network(self):
        L.fetch_kit()
        self.network.reset_mock()
        L.fetch_kit()
        self.network.assert_not_called()

    def test_cached_verified_archive_can_repair_a_damaged_installed_file(self):
        result = L.fetch_kit()
        (Path(result["kit"]) / "ios1-apps/catalog.json").write_bytes(b"damaged")
        self.network.reset_mock()
        L.fetch_kit()
        self.network.assert_not_called()
        self.assertEqual((Path(result["kit"]) / "ios1-apps/catalog.json").read_bytes(), b"[]")

    def test_bad_download_preserves_active_kit_and_discards_partial(self):
        active = self.root / "data/kit-assets"
        old = self.root / "data/old-kit"
        old.mkdir(parents=True)
        (old / "existing").write_text("keep")
        active.symlink_to(old)
        self.network.return_value = Response(b"bad")
        with self.assertRaisesRegex(assets.AssetError, "checksum"):
            L.fetch_kit()
        self.assertEqual(active.resolve(), old.resolve())
        self.assertEqual((old / "existing").read_text(), "keep")
        self.assertFalse(list((self.root / "cache/setup").glob("*.part")))

    def test_interruption_removes_partial_download(self):
        self.network.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            L.fetch_kit()
        self.assertFalse(list((self.root / "cache/setup").glob("*.part")))
        self.assertFalse((self.root / "data/kit-assets").exists())

    def test_unknown_zip_paths_cannot_escape_staging(self):
        bad = self.root / "bad.zip"
        bad.write_bytes(self.zip(dict(self.files, **{"../../outside": b"bad"})))
        target = self.root / "staging"
        target.mkdir()
        with self.assertRaisesRegex(assets.AssetError, "unexpected"):
            L._unpack(bad, self.record, target)
        self.assertFalse(list(target.iterdir()))
        self.assertFalse((self.root / "outside").exists())

    def test_symlink_entries_cannot_be_extracted(self):
        bad = self.root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as archive:
            for name, data in self.files.items():
                info = zipfile.ZipInfo(L.ARCHIVE_ROOT + name)
                info.external_attr = 0o120777 << 16
                archive.writestr(info, data)
        target = self.root / "staging"
        target.mkdir()
        with self.assertRaisesRegex(assets.AssetError, "invalid file"):
            L._unpack(bad, self.record, target)

    def test_member_digest_failure_does_not_install(self):
        blob = self.zip(dict(self.files, **{"ios1-apps/catalog.json": b"{}"}))
        self.record.update(size=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        self.network.return_value = Response(blob)
        with self.assertRaisesRegex(assets.AssetError, "checksum"):
            L.fetch_kit()
        self.assertFalse((self.root / "data/kit-assets").exists())
        self.assertFalse(list((self.root / "data/legacy-kits").iterdir()))

    def test_insecure_redirect_is_rejected(self):
        self.network.return_value.geturl = lambda: "http://example/setup.zip"
        with self.assertRaisesRegex(assets.AssetError, "insecure"):
            L.fetch_kit()
        self.assertFalse((self.root / "data/kit-assets").exists())

    def test_progress_reports_setup_bundle(self):
        progress = []
        L.fetch_kit(lambda *x: progress.append(x))
        self.assertEqual(progress[-1], ("kit", len(self.blob), len(self.blob)))

    def test_setup_prepares_only_10_and_never_opens_a_phone(self):
        with patch.object(assets, "fetch_firmware", return_value={}) as firmware, \
             patch.object(ramdisk, "check_assets", return_value=[]), \
             patch.object(cli.U, "open_transport", side_effect=AssertionError("phone contacted")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["setup"]), 0)
            firmware.assert_called_once()
            self.assertEqual(firmware.call_args.args[0], "1.0")
            self.assertTrue(json.loads(output.getvalue())["ready"])

    def test_two_setup_jobs_cannot_modify_the_active_kit_concurrently(self):
        data = self.root / "data"
        data.mkdir()
        with (data / "setup.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(assets.AssetError, "already running"):
                L.fetch_kit()
        self.network.assert_not_called()

    def test_cancellation_at_pointer_publication_cannot_delete_the_active_version(self):
        original_replace = L.os.replace
        def interrupt_after_publication(source, destination):
            original_replace(source, destination)
            if Path(destination).name == "kit-assets":
                raise KeyboardInterrupt
        with patch.object(L.os, "replace", side_effect=interrupt_after_publication):
            with self.assertRaises(KeyboardInterrupt):
                L.fetch_kit()
        active = self.root / "data/kit-assets"
        self.assertTrue(active.is_dir())
        self.assertTrue(L._verified_tree(active, self.record))
        self.network.reset_mock()
        L.fetch_kit()
        self.network.assert_not_called()


class ManifestTests(unittest.TestCase):
    def test_packaged_manifest_has_only_relative_paths_and_matches_expanded_size(self):
        record = L.manifest()
        self.assertEqual(record["uncompressed_size"], sum(e["size"] for e in record["files"].values()))
        self.assertNotIn(ramdisk.ASSETS_KC, record["files"])

    def test_bad_manifest_file_list_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"schema": 1, "files": ["wrong type"]}))
            with patch.object(L, "MANIFEST", path):
                with self.assertRaisesRegex(assets.AssetError, "manifest is damaged"):
                    L.manifest()

    def test_unsafe_cache_filename_is_rejected(self):
        record = L.manifest()
        record["filename"] = "../../outside.zip"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(record))
            with patch.object(L, "MANIFEST", path):
                with self.assertRaisesRegex(assets.AssetError, "manifest is damaged"):
                    L.manifest()


if __name__ == "__main__":
    unittest.main()
