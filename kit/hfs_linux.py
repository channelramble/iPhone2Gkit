"""Rootless HFSX ramdisk editing for Linux, using pinned xpwn ``hfsplus``.

Clone the verified base image instead of extractall/addall: those xpwn commands
discard Unix modes and turn symlinks into regular files. Only the generated
installer, tools, and payload archives are overlaid. A bounded read-only parser
verifies the catalog and every retained/staged data fork after editing, because
some historical xpwn errors exit with status zero.

Format reference: https://developer.apple.com/library/archive/technotes/tn/tn1150.html
Tool source: https://github.com/planetbeing/xpwn/tree/20c32e5c12d1b22a9d55a59a0ff6267f539b77f4/hfs
"""
import os
import shutil
import stat
import struct
import subprocess
import tempfile
from dataclasses import dataclass

from .ramdisk import BuildError

# Integration can catch this name without weakening the engine's BuildError contract.
HFSError = BuildError

MAX_IMAGE = 32 * 1024 * 1024
TOOL_TIMEOUT = 120


def _u16(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def _path(value):
    value = "/" + str(value).lstrip("/")
    if (value == "/" or len(value.encode("utf-8")) >= 1000 or
            any(x in ("", ".", "..") for x in value[1:].split("/")) or
            any(x in value for x in ("\0", "\n", "\r"))):
        raise BuildError("unsafe ramdisk path: %r" % value)
    return value


@dataclass(frozen=True)
class Entry:
    path: str
    cnid: int
    mode: int
    uid: int
    gid: int
    fork: bytes
    record_offset: int


class Image:
    """Strict reader for small plain HFS+/HFSX images, including overflow extents."""

    def __init__(self, path):
        self.path = os.fspath(path)
        try:
            size = os.path.getsize(path)
            if size < 2048 or size > MAX_IMAGE:
                raise BuildError("ramdisk image is outside the supported size range")
            with open(path, "rb") as f:
                self.data = f.read(MAX_IMAGE + 1)
            header = self.data[1024:1536]
            if header[:2] not in (b"H+", b"HX"):
                raise BuildError("ramdisk has no raw HFS+ volume header")
            if _u16(header, 2) != (5 if header[:2] == b"HX" else 4):
                raise BuildError("unsupported HFS+ volume version")
            if _u32(header, 4) & 0x2000:
                raise BuildError("ramdisk volume is journaled")
            self.block_size, self.blocks, self.free_blocks = struct.unpack_from(">III", header, 40)
            if (self.block_size < 512 or self.block_size > 65536 or
                    self.block_size & (self.block_size - 1) or
                    self.blocks * self.block_size != len(self.data) or
                    self.free_blocks > self.blocks):
                raise BuildError("invalid HFS+ ramdisk geometry")
            # The genuine base keeps older counters/dates in the alternate header.
            # HFS permits that; its signature/version and geometry must agree.
            alternate = self.data[-1024:-512]
            if (alternate[:4] != header[:4] or alternate[40:48] != header[40:48] or
                    _u32(alternate, 4) & 0x2000):
                raise BuildError("HFS+ primary and alternate geometry differ")
            self.overflow = {}
            extents_fork = header[192:272]
            for key, record, _ in self._records(extents_fork, 3):
                if len(key) != 10 or len(record) != 64:
                    raise BuildError("invalid HFS+ overflow extent record")
                fork_type, file_id, start = key[0], _u32(key, 2), _u32(key, 6)
                ident = (file_id, fork_type, start)
                if ident in self.overflow:
                    raise BuildError("duplicate HFS+ overflow extent record")
                self.overflow[ident] = record
            self.catalog_fork = header[272:352]
            self.entries = self._catalog(self.catalog_fork)
        except (OSError, struct.error, UnicodeError, ValueError) as e:
            raise BuildError("cannot read HFS+ ramdisk: %s" % e) from e

    def _extents(self, fork, cnid, fork_type=0):
        if len(fork) != 80:
            raise BuildError("invalid HFS+ data fork")
        logical_size, _, total = struct.unpack_from(">QII", fork)
        extents, covered = [], 0
        record = fork[16:80]
        seen = set()
        while True:
            for start, count in struct.iter_unpack(">II", record):
                if not count:
                    if start:
                        raise BuildError("invalid empty HFS+ extent")
                    continue
                if start + count > self.blocks or covered + count > total:
                    raise BuildError("HFS+ extent is outside the image")
                extents.append((start, count))
                covered += count
            if covered == total:
                break
            ident = (cnid, fork_type, covered)
            if ident in seen or ident not in self.overflow:
                raise BuildError("missing or cyclic HFS+ overflow extents")
            seen.add(ident)
            record = self.overflow[ident]
        if logical_size > covered * self.block_size:
            raise BuildError("HFS+ data fork exceeds its allocation")
        return logical_size, extents

    def _fork(self, fork, cnid):
        size, extents = self._extents(fork, cnid)
        return b"".join(self.data[s * self.block_size:(s + n) * self.block_size]
                        for s, n in extents)[:size]

    def _physical(self, fork, cnid, offset):
        size, extents = self._extents(fork, cnid)
        if not 0 <= offset < size:
            raise BuildError("HFS+ catalog offset is outside its data fork")
        for start, count in extents:
            span = count * self.block_size
            if offset < span:
                return start * self.block_size + offset
            offset -= span
        raise BuildError("missing HFS+ catalog extent")

    def _records(self, fork, cnid):
        data = self._fork(fork, cnid)
        if not data:
            return
        if len(data) < 120 or data[8] != 1:
            raise BuildError("invalid HFS+ B-tree header")
        count = _u32(data, 20)
        node = _u32(data, 24)
        node_size = _u16(data, 32)
        total_nodes = _u32(data, 36)
        if (node_size < 512 or node_size > 32768 or node_size & (node_size - 1) or
                total_nodes * node_size > len(data)):
            raise BuildError("invalid HFS+ B-tree geometry")
        seen, found = set(), 0
        while node:
            if node in seen or node >= total_nodes:
                raise BuildError("cyclic or out-of-range HFS+ B-tree leaf")
            seen.add(node)
            leaf = data[node * node_size:(node + 1) * node_size]
            if leaf[8] != 255:
                raise BuildError("HFS+ leaf chain contains a non-leaf node")
            records = _u16(leaf, 10)
            table = node_size - 2 * (records + 1)
            if table < 14:
                raise BuildError("invalid HFS+ B-tree offset table")
            offsets = [_u16(leaf, node_size - 2 * (i + 1)) for i in range(records + 1)]
            if (offsets[0] < 14 or offsets[-1] > table or
                    any(a >= b for a, b in zip(offsets, offsets[1:]))):
                raise BuildError("invalid HFS+ B-tree record offsets")
            for start, end in zip(offsets, offsets[1:]):
                record = leaf[start:end]
                key_size = _u16(record, 0)
                split = 2 + key_size
                if split > len(record) or split & 1:
                    raise BuildError("invalid HFS+ B-tree key")
                yield record[2:split], record[split:], node * node_size + start + split
                found += 1
            node = _u32(leaf, 0)
        if found != count:
            raise BuildError("HFS+ B-tree leaf record count differs")

    def _catalog(self, fork):
        records = {}
        for key, data, offset in self._records(fork, 4):
            if len(key) < 6 or 6 + 2 * _u16(key, 4) != len(key):
                raise BuildError("invalid HFS+ catalog key")
            kind = _u16(data, 0)
            if kind in (3, 4):
                continue
            if kind not in (1, 2) or len(data) != (88 if kind == 1 else 248):
                raise BuildError("unsupported HFS+ catalog record")
            cnid = _u32(data, 8)
            if cnid in records:
                raise BuildError("duplicate HFS+ catalog ID")
            records[cnid] = (key[6:].decode("utf-16-be"), _u32(key, 0), data, offset)
        if 2 not in records or _u16(records[2][2], 0) != 1:
            raise BuildError("HFS+ root directory is missing")
        paths = {2: "/"}

        def resolve(cnid, seen):
            if cnid in paths:
                return paths[cnid]
            if cnid in seen or cnid not in records:
                raise BuildError("cyclic or missing HFS+ catalog parent")
            name, parent, _, _ = records[cnid]
            if parent not in records or _u16(records[parent][2], 0) != 1:
                raise BuildError("HFS+ catalog parent is not a directory")
            if "/" in name:
                raise BuildError("unsafe HFS+ catalog name")
            value = resolve(parent, seen | {cnid}).rstrip("/") + "/" + name
            if parent == 2 and name in ("\0\0\0\0HFS+ Private Data", ".HFS+ Private Directory Data\r"):
                # Reserved HFS hard-link directories have control characters by design.
                pass
            else:
                value = _path(value)
            paths[cnid] = value
            return value

        entries = {}
        for cnid, (_, _, record, offset) in records.items():
            path = resolve(cnid, set())
            uid, gid = struct.unpack_from(">II", record, 32)
            mode = _u16(record, 42)
            is_dir = _u16(record, 0) == 1
            if cnid == 2 and mode == 0:
                # The stock image uses unset BSD permissions on its volume root.
                mode = stat.S_IFDIR | 0o755
            if (stat.S_ISDIR(mode) != is_dir or path in entries):
                raise BuildError("HFS+ catalog type/path conflict: %s (mode %o)" % (path, mode))
            entries[path] = Entry(path, cnid, mode, uid, gid,
                                  b"" if is_dir else record[88:168],
                                  offset)
        return entries

    def read(self, path):
        entry = self.entries.get(path)
        if entry is None or stat.S_ISDIR(entry.mode):
            raise BuildError("HFS+ file not found: %s" % path)
        return self._fork(entry.fork, entry.cnid)


def _run(tool, image, *args):
    try:
        p = subprocess.run([os.fspath(tool), os.fspath(image), *args],
                           capture_output=True, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BuildError("HFS+ tool failed: %s" % e) from e
    diagnostic = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode or any(word in diagnostic.lower() for word in (
            "error:", "cannot ", "not enough", "no such", "not a file", "not a folder",
            "assert", "warning:")):
        raise BuildError("HFS+ %s failed: %s" % (args[0], diagnostic.strip()[-2000:]))


def _excluded(path, exclusions):
    return any(path == x or path.startswith(x + "/") for x in exclusions)


def extract_tree(image, destination, excludes=()):
    """Extract data forks, modes and symlinks into a NEW directory, without root.

    Host file ownership is the caller's; Image.entries retains the source UID/GID.
    The build path preserves original in-volume ownership by cloning, not reimport.
    Special files are rejected rather than silently changed into regular files.
    """
    parsed = Image(image)
    exclusions = {_path(p) for p in excludes}
    try:
        os.mkdir(destination)
        entries = [e for e in parsed.entries.values() if e.path != "/" and
                   not e.path.startswith(("/\0\0\0\0HFS+ Private Data", "/.HFS+ Private Directory Data\r")) and
                   not _excluded(e.path, exclusions)]
        for entry in sorted(entries, key=lambda e: (e.path.count("/"), e.path)):
            target = os.path.join(destination, entry.path.lstrip("/"))
            if stat.S_ISDIR(entry.mode):
                os.mkdir(target)
            elif stat.S_ISREG(entry.mode):
                with open(target, "xb") as f:
                    f.write(parsed.read(entry.path))
            elif not stat.S_ISLNK(entry.mode):
                raise BuildError("cannot rootlessly extract special file: %s" % entry.path)
        # Creating links last prevents absolute links from redirecting extraction.
        for entry in entries:
            target = os.path.join(destination, entry.path.lstrip("/"))
            if stat.S_ISLNK(entry.mode):
                os.symlink(os.fsdecode(parsed.read(entry.path)), target)
            else:
                os.chmod(target, stat.S_IMODE(entry.mode))
        os.chmod(destination, stat.S_IMODE(parsed.entries["/"].mode))
    except OSError as e:
        raise BuildError("HFS+ extraction failed: %s" % e) from e
    return parsed.entries


def build_image(base_image, staging_dir, output_image, size, excludes=(), tool="hfsplus"):
    """Clone/grow base, overlay staged files, verify, then atomically publish raw HFSX.

    Native command contract: hfsplus IMAGE {grow BYTES,rm PATH,rmall PATH,
    mkdir PATH,add HOST_FILE PATH,symlink PATH TARGET}. No mount, FUSE or sudo.
    All staged entries become UID/GID 0 while retaining their modes; untouched
    base entries keep their original permissions, links, metadata and contents.
    """
    base = Image(base_image)
    if not isinstance(size, int) or isinstance(size, bool) or size < len(base.data) or size > MAX_IMAGE:
        raise BuildError("Linux ramdisk size must be at least the base image size and at most 32 MB")
    if size % base.block_size:
        raise BuildError("ramdisk size is not allocation-block aligned")
    exclusions = {_path(p) for p in excludes}
    staged = []
    try:
        if not os.path.isdir(staging_dir) or os.path.islink(staging_dir):
            raise BuildError("ramdisk staging directory is missing or is a symlink")
        for root, dirs, files in os.walk(staging_dir, followlinks=False):
            for name in sorted(dirs + files):
                host = os.path.join(root, name)
                path = _path(os.path.relpath(host, staging_dir).replace(os.sep, "/"))
                mode = os.lstat(host).st_mode
                if not any(test(mode) for test in (stat.S_ISDIR, stat.S_ISREG, stat.S_ISLNK)):
                    raise BuildError("unsupported staged file: %s" % path)
                if stat.S_ISREG(mode) and os.path.getsize(host) > size:
                    raise BuildError("staged file exceeds ramdisk size: %s" % path)
                staged.append((path, host, mode))
        staged.sort(key=lambda item: (item[0].count("/"), item[0]))
        output_image = os.path.abspath(output_image)
        if os.path.realpath(output_image) == os.path.realpath(base_image):
            raise BuildError("ramdisk output would overwrite the base image")
        os.makedirs(os.path.dirname(output_image), exist_ok=True)
        handle, pending = tempfile.mkstemp(prefix=".ios1kit-hfs-", dir=os.path.dirname(output_image))
        os.close(handle)
        try:
            shutil.copyfile(base_image, pending)
            if size > len(base.data):
                _run(tool, pending, "grow", str(size))
            current = Image(pending)
            if len(current.data) != size:
                raise BuildError("HFS+ grow did not produce the requested image size")
            for path in sorted(exclusions, key=lambda x: (x.count("/"), x)):
                current = Image(pending)
                entry = current.entries.get(path)
                if entry is not None:
                    _run(tool, pending, "rmall" if stat.S_ISDIR(entry.mode) else "rm", path)
            for path, host, mode in staged:
                current = Image(pending)
                parent = path.rsplit("/", 1)[0] or "/"
                if parent not in current.entries or not stat.S_ISDIR(current.entries[parent].mode):
                    raise BuildError("staged parent is missing or a symlink: %s" % parent)
                old = current.entries.get(path)
                if stat.S_ISDIR(mode):
                    if old is None:
                        _run(tool, pending, "mkdir", path)
                    elif not stat.S_ISDIR(old.mode):
                        raise BuildError("staged directory conflicts with base file: %s" % path)
                else:
                    if old is not None:
                        if stat.S_ISDIR(old.mode):
                            raise BuildError("staged file conflicts with base directory: %s" % path)
                        _run(tool, pending, "rm", path)
                    if stat.S_ISLNK(mode):
                        _run(tool, pending, "symlink", path, os.readlink(host))
                    else:
                        _run(tool, pending, "add", host, path)
            # Set BSD permissions directly in catalog records; xpwn chmod follows
            # symlinks and cannot reliably set link modes or root ownership.
            current = Image(pending)
            with open(pending, "r+b") as f:
                for path, _, mode in staged:
                    entry = current.entries.get(path)
                    if entry is None or stat.S_IFMT(entry.mode) != stat.S_IFMT(mode):
                        raise BuildError("HFS+ tool did not create expected entry: %s" % path)
                    for start, value in ((32, struct.pack(">II", 0, 0)),
                                         (42, struct.pack(">H", mode & 0xffff))):
                        # Catalog records may cross noncontiguous allocation extents.
                        for index, byte in enumerate(value):
                            f.seek(current._physical(current.catalog_fork, 4,
                                                     entry.record_offset + start + index))
                            f.write(bytes((byte,)))
                f.flush()
                os.fsync(f.fileno())
            final = Image(pending)
            changed = {p for p, _, _ in staged}
            for path, entry in base.entries.items():
                if _excluded(path, exclusions) or path in changed:
                    continue
                got = final.entries.get(path)
                if got is None or (got.mode, got.uid, got.gid) != (entry.mode, entry.uid, entry.gid):
                    raise BuildError("retained HFS+ entry changed: %s" % path)
                if not stat.S_ISDIR(entry.mode) and final.read(path) != base.read(path):
                    raise BuildError("retained HFS+ data fork changed: %s" % path)
            if any(_excluded(p, exclusions) and p not in changed for p in final.entries):
                raise BuildError("excluded HFS+ entries remain in the image")
            for path, host, mode in staged:
                entry = final.entries[path]
                if (entry.mode, entry.uid, entry.gid) != (mode & 0xffff, 0, 0):
                    raise BuildError("staged HFS+ permissions do not match: %s" % path)
                if not stat.S_ISDIR(mode):
                    if stat.S_ISLNK(mode):
                        expected = os.fsencode(os.readlink(host))
                    else:
                        with open(host, "rb") as f:
                            expected = f.read(MAX_IMAGE + 1)
                    if final.read(path) != expected:
                        raise BuildError("staged HFS+ file contents do not match: %s" % path)
            os.replace(pending, output_image)
            return output_image
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
    except OSError as e:
        raise BuildError("Linux ramdisk construction failed: %s" % e) from e
