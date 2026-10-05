"""Rootless HFS+ tests; optional genuine fixture runs on either macOS or Linux.

IOS1KIT_HFS_BASE points to the verified raw iLibertyRD.dat, and HFSPLUS points
to a source-built xpwn executable. CI can keep that proprietary input private;
no Apple firmware or iLiberty assets are copied into this test source.
"""
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kit import hfs_linux as H
from kit.ramdisk import BuildError, RD_DAT_SHA, sha256


def fork(size, start, count):
    data = bytearray(80)
    struct.pack_into(">QII", data, 0, size, 0, count)
    struct.pack_into(">II", data, 16, start, count)
    return bytes(data)


def catalog_record(name, parent, cnid, mode, data_fork=b"", uid=0, gid=0):
    encoded = name.encode("utf-16-be")
    key = struct.pack(">I H", parent, len(encoded) // 2) + encoded
    record = bytearray(88 if stat.S_ISDIR(mode) else 248)
    struct.pack_into(">H", record, 0, 1 if stat.S_ISDIR(mode) else 2)
    struct.pack_into(">I", record, 8, cnid)
    struct.pack_into(">II", record, 32, uid, gid)
    struct.pack_into(">H", record, 42, mode)
    if data_fork:
        record[88:168] = data_fork
    return struct.pack(">H", len(key)) + key + record


def fixture():
    """An entirely synthetic HFSX volume with regular and symbolic-link entries."""
    image = bytearray(16384)
    header = bytearray(512)
    header[:4] = b"HX\0\5"
    struct.pack_into(">III", header, 40, 512, 32, 12)
    header[272:352] = fork(2048, 4, 4)
    image[1024:1536] = header
    image[-1024:-512] = header
    tree = bytearray(2048)
    tree[8] = 1
    struct.pack_into(">IIIHHI", tree, 20, 4, 1, 1, 1024, 516, 2)
    leaf = memoryview(tree)[1024:2048]
    leaf[8] = 255
    struct.pack_into(">H", leaf, 10, 4)
    records = [
        catalog_record("test", 1, 2, stat.S_IFDIR | 0o755),
        catalog_record("etc", 2, 16, stat.S_IFDIR | 0o700, uid=12, gid=14),
        catalog_record("hello", 2, 17, stat.S_IFREG | 0o640, fork(6, 12, 1), uid=4, gid=5),
        catalog_record("link", 2, 18, stat.S_IFLNK | 0o777, fork(6, 13, 1)),
    ]
    offset = 14
    for i, record in enumerate(records):
        struct.pack_into(">H", leaf, 1024 - 2 * (i + 1), offset)
        leaf[offset:offset + len(record)] = record
        offset += len(record)
    struct.pack_into(">H", leaf, 1024 - 2 * (len(records) + 1), offset)
    image[2048:4096] = tree
    image[6144:6150] = b"hello\n"
    image[6656:6662] = b"/hello"
    return image


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "base.hfs")
        self.write(fixture())

    def write(self, data):
        with open(self.base, "wb") as f:
            f.write(data)

    def test_catalog_contents_modes_and_symlink_target(self):
        image = H.Image(self.base)
        self.assertEqual(image.read("/hello"), b"hello\n")
        self.assertEqual(image.read("/link"), b"/hello")
        self.assertEqual((image.entries["/hello"].mode, image.entries["/hello"].uid,
                          image.entries["/hello"].gid), (stat.S_IFREG | 0o640, 4, 5))
        self.assertTrue(stat.S_ISLNK(image.entries["/link"].mode))

    def test_rootless_extract_preserves_modes_and_links(self):
        destination = os.path.join(self.tmp.name, "tree")
        entries = H.extract_tree(self.base, destination)
        self.assertEqual(os.readlink(os.path.join(destination, "link")), "/hello")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(destination, "hello")).st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(destination, "etc")).st_mode), 0o700)
        self.assertEqual(entries["/hello"].uid, 4)

    def test_reject_journaled_image_and_invalid_geometry(self):
        for position, value in ((1028, 0x2000), (1064, 0), (1068, 999)):
            data = fixture()
            struct.pack_into(">I", data, position, value)
            self.write(data)
            with self.assertRaises(BuildError):
                H.Image(self.base)

    def test_reject_out_of_image_data_extent(self):
        data = fixture()
        # Alter hello's fork extent by locating its catalog record through the reader.
        image = H.Image(self.base)
        offset = image._physical(image.catalog_fork, 4, image.entries["/hello"].record_offset)
        struct.pack_into(">I", data, offset + 104, 1000)
        self.write(data)
        with self.assertRaisesRegex(BuildError, "outside"):
            H.Image(self.base).read("/hello")

    def test_reject_cyclic_btree_before_extraction(self):
        data = fixture()
        struct.pack_into(">I", data, 3072, 1)
        self.write(data)
        with self.assertRaisesRegex(BuildError, "cyclic"):
            H.Image(self.base)

    def test_reject_catalog_path_traversal(self):
        data = fixture()
        # Name length differs, so reconstruct the file entry with the same UTF-16 length.
        image = H.Image(self.base)
        entry = image.entries["/hello"]
        record_start = image._physical(image.catalog_fork, 4, entry.record_offset)
        key_start = record_start - 18
        data[key_start + 8:key_start + 18] = "../xx".encode("utf-16-be")
        self.write(data)
        with self.assertRaises(BuildError):
            H.Image(self.base)

    def test_reject_missing_overflow_record(self):
        data = fixture()
        image = H.Image(self.base)
        record_start = image._physical(image.catalog_fork, 4, image.entries["/hello"].record_offset)
        struct.pack_into(">I", data, record_start + 100, 2)
        self.write(data)
        with self.assertRaisesRegex(BuildError, "overflow"):
            H.Image(self.base).read("/hello")

    def test_failed_tool_does_not_replace_existing_output(self):
        stage, output = os.path.join(self.tmp.name, "stage"), os.path.join(self.tmp.name, "output.hfs")
        os.mkdir(stage)
        with open(output, "wb") as f:
            f.write(b"prior-output")
        with patch.object(H, "_run", side_effect=BuildError("tool failure")):
            with self.assertRaisesRegex(BuildError, "tool failure"):
                H.build_image(self.base, stage, output, 32768)
        with open(output, "rb") as f:
            self.assertEqual(f.read(), b"prior-output")
        self.assertFalse(any(name.startswith(".ios1kit-hfs-") for name in os.listdir(self.tmp.name)))

    def test_status_zero_grow_failure_is_detected(self):
        stage = os.path.join(self.tmp.name, "stage")
        os.mkdir(stage)
        with patch.object(H, "_run"):
            with self.assertRaisesRegex(BuildError, "requested image size"):
                H.build_image(self.base, stage, os.path.join(self.tmp.name, "output"), 32768)

    def test_cannot_overwrite_original_image_or_shrink(self):
        stage = os.path.join(self.tmp.name, "stage")
        os.mkdir(stage)
        for size, output in ((8192, self.base + ".out"), (16384, self.base)):
            with self.assertRaises(BuildError):
                H.build_image(self.base, stage, output, size)

    def test_timeout_and_zero_status_diagnostic_are_errors(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("hfsplus", 1)):
            with self.assertRaises(BuildError):
                H._run("hfsplus", self.base, "grow", "32768")
        fake = subprocess.CompletedProcess([], 0, b"Cannot shrink volume\n", b"")
        with patch.object(subprocess, "run", return_value=fake):
            with self.assertRaises(BuildError):
                H._run("hfsplus", self.base, "grow", "8192")


@unittest.skipUnless(os.environ.get("IOS1KIT_HFS_BASE") and os.environ.get("HFSPLUS"),
                     "set IOS1KIT_HFS_BASE and HFSPLUS for genuine image integration")
class GenuineImageTests(unittest.TestCase):
    def setUp(self):
        self.base = os.environ["IOS1KIT_HFS_BASE"]
        self.tool = os.environ["HFSPLUS"]
        self.assertEqual(sha256(self.base), RD_DAT_SHA, "genuine fixture checksum")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_genuine_image_grow_overlay_permissions_and_links(self):
        stage = os.path.join(self.tmp.name, "stage")
        os.makedirs(os.path.join(stage, "etc"))
        os.makedirs(os.path.join(stage, "bin"))
        os.makedirs(os.path.join(stage, "payloads"))
        for path, data, mode in (("etc/profile", b"echo linux\n", 0o644),
                                 ("bin/test-tool", b"test-tool-data\n", 0o755),
                                 ("payloads/fixture.zip", b"large-payload\0" * 200000, 0o640)):
            host = os.path.join(stage, path)
            with open(host, "wb") as f:
                f.write(data)
            os.chmod(host, mode)
        os.symlink("/bin/sh", os.path.join(stage, "bin", "test-sh"))
        base_hash = sha256(self.base)
        for mb in (14, 22):
            output = os.path.join(self.tmp.name, "%d.hfs" % mb)
            H.build_image(self.base, stage, output, mb * 1024 * 1024,
                          excludes=("aviegas", ".fseventsd", ".Trashes", "etc/profile"), tool=self.tool)
            image = H.Image(output)
            self.assertEqual(os.path.getsize(output), mb * 1024 * 1024)
            self.assertEqual(image.read("/etc/profile"), b"echo linux\n")
            self.assertEqual(image.read("/bin/test-sh"), b"/bin/sh")
            self.assertTrue(stat.S_ISLNK(image.entries["/bin/test-sh"].mode))
            self.assertEqual((image.entries["/payloads/fixture.zip"].uid,
                              image.entries["/payloads/fixture.zip"].gid,
                              image.entries["/payloads/fixture.zip"].mode), (0, 0, stat.S_IFREG | 0o640))
            self.assertEqual(image.entries["/bin/chmod"].mode, stat.S_IFREG | 0o555)
            self.assertNotIn("/aviegas", image.entries)
            self.assertEqual(image.read("/System/Library/Frameworks/IOKit.framework/Versions/Current"), b"A")
        self.assertEqual(sha256(self.base), base_hash)

    def test_genuine_ten_mb_probe_no_growth(self):
        stage = os.path.join(self.tmp.name, "stage")
        os.makedirs(os.path.join(stage, "etc"))
        with open(os.path.join(stage, "etc", "profile"), "w") as f:
            f.write("echo probe\n")
        output = os.path.join(self.tmp.name, "probe.hfs")
        H.build_image(self.base, stage, output, 10 * 1024 * 1024,
                      excludes=("aviegas", ".fseventsd", ".Trashes", "etc/profile"), tool=self.tool)
        self.assertEqual(H.Image(output).read("/etc/profile"), b"echo probe\n")


if __name__ == "__main__":
    unittest.main()
