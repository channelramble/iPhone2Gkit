"""Exercise the built legacy command function with a simulated USB receiver."""
import os
import shutil
import subprocess
import tempfile
import unittest

SOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                      "macos/vendor/build/libirecovery-1.3.1/src/libirecovery.c")


@unittest.skipUnless(os.path.isfile(SOURCE) and shutil.which("clang"), "build USB dependencies first")
class LegacyPacketTests(unittest.TestCase):
    def test_command_boundaries_include_newline_and_nul(self):
        with open(SOURCE) as f:
            source = f.read()
        start = source.index("static irecv_error_t irecv_send_command_raw(")
        end = source.index("\n}\n#endif", start) + 2
        function = source[start:end]
        # Run the actual function, replacing only the device and USB calls.
        preamble = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int irecv_error_t;
struct client { struct { unsigned cpid; uint64_t ecid; } device_info; };
typedef struct client *irecv_client_t;
typedef struct { uint16_t cmdcode, magic; uint32_t size, loadaddr; } legacyCMD;
#define IRECV_E_INVALID_INPUT -1
#define IRECV_E_UNKNOWN_ERROR -2
#define IRECV_E_SUCCESS 0
#define MSG_SEND_COMMAND 0x801
#define MSG_ACK 0x808
#define USB_TIMEOUT 10000
#define debug(...) ((void)0)
static unsigned logical, requested, received;
unsigned sleep(unsigned seconds) { return 0; }
int irecv_usb_interrupt_transfer(irecv_client_t c, unsigned char ep,
        unsigned char *data, int size, int *bytes, unsigned timeout) {
    *bytes = size;
    if (ep == 4) requested = ((legacyCMD *)data)->size;
    else if (ep == 0x83) ((legacyCMD *)data)->cmdcode = MSG_ACK;
    else if (ep == 2) {
        if (size != requested || size % 16 || size > 256 || logical + 2 > size ||
            data[logical] != '\n' || data[logical + 1] != 0) return -3;
        for (unsigned i = 0; i < logical; i++) if (data[i] != 'x') return -4;
        received++;
    } else return -5;
    return 0;
}
int irecv_usb_control_transfer(irecv_client_t c, int a, int b, int d, int e,
        unsigned char *data, int size, unsigned timeout) { return -6; }
'''
        main = r'''
int main(void) {
    struct client c = {{0x8900, 0}};
    char command[256];
    for (logical = 1; logical <= 254; logical++) {
        memset(command, 'x', logical); command[logical] = 0;
        if (irecv_send_command_raw(&c, command, 0)) return 1;
    }
    memset(command, 'x', 255); command[255] = 0;
    if (irecv_send_command_raw(&c, command, 0) != IRECV_E_INVALID_INPUT) return 2;
    return received == 254 ? 0 : 3;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            exe = os.path.join(directory, "legacy-packets")
            build = subprocess.run(["clang", "-x", "c", "-std=c99", "-o", exe, "-"],
                                   input=preamble + function + main, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
