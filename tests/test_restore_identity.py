"""Run the patched native restore selector against simulated restored replies.

The tested function is extracted from the checksum-pinned upstream tarball after
applying the app's current patch. Only the USB/service calls are mocked; libplist
is the real static library used by the bundled programs. No phone is opened.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "macos/vendor/restore/src/idevicerestore-c25aefd.tar.gz"
ARCHIVE_SHA256 = "266e1f444d97fcdd78227eba8c4b252803ef3680d3ad6c1a3e5f286fce96ac29"
SOURCE_ROOT = "idevicerestore-c25aefd49b3769c2907875e15566d433d59bd979"
PATCH = ROOT / "macos/restore-build.patch"
PREFIX = ROOT / "macos/vendor/build/prefix"

PREAMBLE = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <plist/plist.h>
typedef void *idevice_t;
typedef void *restored_client_t;
typedef int restored_error_t;
#define IDEVICE_E_SUCCESS 0
#define RESTORE_E_SUCCESS 0
#define PLIST_UINT PLIST_INT
#define debug(...) ((void)0)
struct target { unsigned chip_id, board_id; const char *product_type; };
struct idevicerestore_client_t {
    struct target *device;
    uint64_t ecid;
    const char *version, *srnm;
};
static plist_t hardware, cached_serial;
static const char *reply_type = "com.apple.mobile.restored";
static uint64_t protocol = 7;
static int hardware_queries;
static int idevice_new(idevice_t *v, const char *u) { *v = (void *)1; return 0; }
static void idevice_free(idevice_t v) {}
static int restored_client_new(idevice_t v, restored_client_t *r, const char *l) {
    *r = (void *)2; return 0;
}
static void restored_client_free(restored_client_t r) {}
static int restored_query_type(restored_client_t r, char **s, uint64_t *v) {
    *s = strdup(reply_type); *v = protocol; return 0;
}
static int restored_query_value(restored_client_t r, const char *k, plist_t *v) {
    hardware_queries++;
    *v = hardware ? plist_copy(hardware) : NULL;
    return hardware ? 0 : 1;
}
static int restored_get_value(restored_client_t r, const char *k, plist_t *v) {
    *v = cached_serial ? plist_copy(cached_serial) : NULL;
    return cached_serial ? 0 : 1;
}
'''

MAIN = r'''
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int scenario = atoi(argv[1]);
    struct target target = {0x8900, 0, "iPhone1,1"};
    struct idevicerestore_client_t client = {&target, 0, "1.0", "MATCHING"};
    hardware = plist_new_dict();
    cached_serial = plist_new_string("MATCHING");
    plist_dict_set_item(hardware, "ChipID", plist_new_uint(0x8900));
    plist_dict_set_item(hardware, "BoardID", plist_new_uint(0));
    switch (scenario) {
    case 0: break; /* Exact expected identity. */
    case 1: reply_type = "com.apple.mobile.lockdown"; break;
    case 2: plist_dict_set_item(hardware, "ChipID", plist_new_uint(0x8015)); break;
    case 3: plist_dict_set_item(hardware, "BoardID", plist_new_uint(2)); break;
    case 4: plist_dict_set_item(hardware, "UniqueChipID", plist_new_uint(1234)); break;
    case 5:
        plist_free(cached_serial); cached_serial = plist_new_string("DIFFERENT"); break;
    case 6: plist_free(hardware); hardware = NULL; break;
    case 7:
        plist_free(hardware); hardware = NULL; client.version = "3.1.3"; break;
    case 8: client.version = "3.1.3"; break;
    case 9:
        plist_free(hardware); hardware = plist_new_string("invalid"); break;
    case 10: plist_dict_set_item(hardware, "ChipID", plist_new_string("0x8900")); break;
    case 11: plist_free(hardware); hardware = NULL; protocol = 8; break;
    case 12: plist_free(hardware); hardware = NULL; protocol = 13; break;
    case 13: target.product_type = "iPod1,1"; break;
    default: return 2;
    }
    int matched = restore_is_current_device(&client, "SIMULATED-UDID");
    if (hardware) plist_free(hardware);
    if (cached_serial) plist_free(cached_serial);
    printf("%d %d\n", matched, hardware_queries);
    return 0;
}
'''


class NativeRestoreIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler, patch_tool = shutil.which("clang"), shutil.which("patch")
        library = PREFIX / "lib/libplist-2.0.a"
        header = PREFIX / "include/plist/plist.h"
        if sys.platform != "darwin" or not compiler or not patch_tool:
            raise unittest.SkipTest("native restore identity checks require macOS, clang and patch")
        if not all(path.is_file() for path in (ARCHIVE, PATCH, library, header)):
            raise unittest.SkipTest("run macos/fetch-deps.sh and macos/fetch-restore-deps.sh first")
        if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise AssertionError("pinned idevicerestore source archive checksum mismatch")

        directory = tempfile.TemporaryDirectory(prefix="iphone2gkit-identity-test-")
        cls.addClassCleanup(directory.cleanup)
        work = Path(directory.name)
        # Read only the fixed files touched by our patch; never extract arbitrary
        # archive paths, links or modes into the host filesystem.
        source_files = ("idevicerestore.c", "normal.c", "dfu.c", "recovery.c", "restore.c")
        (work / "src").mkdir()
        with tarfile.open(ARCHIVE, "r:gz") as archive:
            for name in source_files:
                stream = archive.extractfile(f"{SOURCE_ROOT}/src/{name}")
                if stream is None:
                    raise AssertionError(f"pinned archive lacks src/{name}")
                (work / "src" / name).write_bytes(stream.read())
        applied = subprocess.run(
            [patch_tool, "-p1", "--batch", "-i", str(PATCH)],
            cwd=work, text=True, capture_output=True, timeout=20,
        )
        if applied.returncode:
            raise AssertionError(applied.stdout + applied.stderr)
        source = (work / "src/restore.c").read_text()
        start = source.index("static int restore_is_current_device(")
        end = source.index("\nint restore_open_with_timeout(", start)
        function = source[start:end]
        cls.executable = work / "restore-identity-check"
        built = subprocess.run(
            [compiler, "-x", "c", "-O2", "-mmacosx-version-min=13.0",
             "-I", str(PREFIX / "include"), "-o", str(cls.executable),
             "-", "-x", "none", str(library)],
            input=PREAMBLE + function + MAIN, text=True, capture_output=True, timeout=30,
        )
        if built.returncode:
            raise AssertionError(built.stderr)

    def check_scenario(self, scenario, match, queries=1):
        result = subprocess.run(
            [str(self.executable), str(scenario)], capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{match} {queries}")

    def test_expected_identity_queries_hardware(self):
        self.check_scenario(0, 1)

    def test_non_restored_service_rejected(self):
        self.check_scenario(1, 0, 0)

    def test_wrong_chip_rejected_despite_zero_ecid(self):
        self.check_scenario(2, 0)

    def test_wrong_board_rejected(self):
        self.check_scenario(3, 0)

    def test_wrong_ecid_rejected(self):
        self.check_scenario(4, 0)

    def test_serial_changed_after_boot_rejected(self):
        self.check_scenario(5, 0)

    def test_protocol7_legacy_missing_hardware_allowed(self):
        self.check_scenario(6, 1)

    def test_modern_target_missing_hardware_rejected(self):
        self.check_scenario(7, 0)

    def test_modern_target_with_matching_hardware_allowed(self):
        self.check_scenario(8, 1)

    def test_malformed_hardware_info_rejected(self):
        self.check_scenario(9, 0)

    def test_malformed_chip_field_rejected(self):
        self.check_scenario(10, 0)

    def test_protocol8_legacy_missing_hardware_allowed(self):
        self.check_scenario(11, 1)

    def test_undocumented_protocol_missing_hardware_rejected(self):
        self.check_scenario(12, 0)

    def test_wrong_initial_product_rejected_before_queries(self):
        self.check_scenario(13, 0, 0)
