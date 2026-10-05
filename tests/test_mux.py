"""Read-only multiplexing preflight, tested against a local socket server."""
import os
import contextlib
import io
from pathlib import Path
import plistlib
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kit import mux as M


class Server:
    def __init__(self, handler):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "mux")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(1)
        self.sock.settimeout(2)
        self.errors = []
        def run():
            try:
                with self.sock.accept()[0] as client:
                    client.settimeout(1)
                    handler(client)
            except Exception as e:
                self.errors.append(e)
        self.thread = threading.Thread(target=run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.thread.join(3)
        self.sock.close()
        self.temp.cleanup()
        if self.errors:
            raise self.errors[0]


def receive(client, header="<IIII"):
    size = struct.calcsize(header)
    fields = struct.unpack(header, M._read(client, size, time.monotonic() + 1))
    length = fields[0] - size if size == 16 else fields[0]
    return plistlib.loads(M._read(client, length, time.monotonic() + 1))


def reply(client, data, fragment=False):
    payload = plistlib.dumps(data)
    raw = M.HEADER.pack(16 + len(payload), 1, 8, 1) + payload
    if fragment:
        for i in range(0, len(raw), 7):
            client.sendall(raw[i:i + 7])
    else:
        client.sendall(raw)


def record(handle=1, pid=0x1290, connection="USB", serial="a" * 40):
    return {"DeviceID": handle, "Properties": {"ProductID": pid, "ConnectionType": connection,
                                               "SerialNumber": serial}}


class ProtocolTests(unittest.TestCase):
    def test_fragmented_reply_and_network_devices_ignored(self):
        def handler(client):
            message = receive(client)
            self.assertEqual(message["MessageType"], "ListDevices")
            self.assertNotIn("PairRecord", message)
            reply(client, {"DeviceList": [record(), record(2, connection="Network")]}, True)
        with Server(handler) as server, patch.dict(os.environ, {"USBMUXD_SOCKET_ADDRESS": "evil:123"}):
            devices = M.list_devices(socket_path=server.path)
        self.assertEqual([d["device_id"] for d in devices], [1])

    def test_invalid_header_rejected_without_large_allocation(self):
        for fields in ((M.MAX_REPLY + 1, 1, 8, 1), (20, 1, 8, 2), (20, 0, 8, 1)):
            def handler(client):
                receive(client)
                client.sendall(M.HEADER.pack(*fields))
            with self.subTest(fields=fields), Server(handler) as server:
                with self.assertRaisesRegex(M.MuxError, "header"):
                    M.list_devices(socket_path=server.path)

    def test_incomplete_header_and_missing_device_list_fail(self):
        def incomplete(client):
            receive(client)
            client.sendall(b"x")
        with Server(incomplete) as server, self.assertRaisesRegex(M.MuxError, "incomplete"):
            M.list_devices(socket_path=server.path)
        def missing(client):
            receive(client)
            reply(client, {"MessageType": "Result", "Number": 1})
        with Server(missing) as server, self.assertRaisesRegex(M.MuxError, "device list"):
            M.list_devices(socket_path=server.path)

    def test_silent_server_has_a_real_deadline(self):
        def handler(client):
            receive(client)
            time.sleep(.15)
        started = time.monotonic()
        with Server(handler) as server, self.assertRaises(M.MuxError):
            M.list_devices(timeout=.05, socket_path=server.path)
        self.assertLess(time.monotonic() - started, .8)

    def test_duplicate_handles_and_boolean_ids_rejected(self):
        for records in ([record(), record()], [record(True)], [record(pid=True)]):
            with self.subTest(records=records), self.assertRaises(M.MuxError):
                M._devices({"DeviceList": records})

    def test_service_query_sends_only_query_type_on_fixed_port(self):
        def handler(client):
            request = receive(client)
            self.assertEqual(request["MessageType"], "Connect")
            self.assertEqual(request["DeviceID"], 1)
            self.assertEqual(request["PortNumber"], socket.htons(62078))
            reply(client, {"MessageType": "Result", "Number": 0})
            request = receive(client, ">I")
            self.assertEqual(request["Request"], "QueryType")
            data = plistlib.dumps({"Type": "com.apple.mobile.restored", "RestoreProtocolVersion": 7})
            client.sendall(struct.pack(">I", len(data)) + data)
        with Server(handler) as server:
            result = M.query_service(1, socket_path=server.path)
        self.assertEqual(result["protocol_version"], 7)

    def test_failed_connect_never_sends_a_phone_request(self):
        def handler(client):
            receive(client)
            reply(client, {"MessageType": "Result", "Number": 3})
            self.assertEqual(client.recv(1), b"")
        with Server(handler) as server, self.assertRaisesRegex(M.MuxError, "not accepting"):
            M.query_service(1, socket_path=server.path)

    def test_unknown_or_malformed_service_is_not_a_pass(self):
        for data in ({"Type": "other"}, {"Type": "com.apple.mobile.restored"}):
            def handler(client):
                receive(client)
                reply(client, {"MessageType": "Result", "Number": 0})
                receive(client, ">I")
                payload = plistlib.dumps(data)
                client.sendall(struct.pack(">I", len(payload)) + payload)
            with self.subTest(data=data), Server(handler) as server, self.assertRaises(M.MuxError):
                M.query_service(1, socket_path=server.path)


class ReadinessTests(unittest.TestCase):
    def test_recovery_and_dfu_are_pending_not_verified(self):
        for mode in ("recovery", "dfu"):
            result = M.assess([{"mode": mode}], [])
            self.assertEqual(result["status"], "pending_restore_mode")
            self.assertFalse(result["visible"])

    def test_visible_usb_pid_without_daemon_visibility_is_not_success(self):
        result = M.assess([{"mode": "normal", "pid": 0x1290}], [])
        self.assertEqual(result["status"], "not_visible")

    def test_usb_serial_mismatch_cannot_select_a_device(self):
        result = M.assess([{"mode": "normal", "pid": 0x1290, "usb_serial": "b" * 40}],
                          M._devices({"DeviceList": [record()]}))
        self.assertEqual(result["status"], "not_visible")

    def test_multiple_physical_phones_never_queries_a_service(self):
        with patch.object(M, "list_devices", return_value=M._devices({"DeviceList": [record()]})), \
             patch.object(M, "query_service") as query:
            result = M.check([{"mode": "normal", "pid": 0x1290}, {"pid": 0x12a8}], True)
        query.assert_not_called()
        self.assertTrue(result["blocking"])

    def test_missing_system_service_is_blocking_and_explicit(self):
        with patch.object(M, "list_devices", side_effect=M.MuxError("service missing")):
            result = M.check([{"mode": "dfu"}])
        self.assertEqual(result["status"], "service_unavailable")
        self.assertTrue(result["blocking"])

    def test_cli_missing_connection_service_cannot_start_restore(self):
        from kit import cli
        plan = {"path": "unused.ipsw", "sha256": "a" * 64, "identity": {},
                "version": "3.1.3", "build": "7E18", "warnings": []}
        with patch.object(cli.R, "prepare", return_value=plan), \
             patch.object(cli.R, "usb_inventory", return_value=[{"mode": "dfu"}]), \
             patch.object(M, "list_devices", side_effect=M.MuxError("service missing")), \
             patch.object(cli.R, "execute") as execute, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(["restore", "-y", "--confirm-original-iphone"])
        self.assertEqual(result, 2)
        execute.assert_not_called()

    def test_cli_inventory_check_does_not_prepare_or_erase_firmware(self):
        from kit import cli
        with patch.object(cli.R, "usb_inventory", return_value=[]), \
             patch.object(M, "list_devices", return_value=[]), \
             patch.object(cli.R, "prepare") as prepare, patch.object(cli.R, "execute") as execute, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = cli.main(["transport-info", "--json"])
        import json
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "no_phone")
        prepare.assert_not_called()
        execute.assert_not_called()

    def test_requested_service_probe_is_not_success_when_it_cannot_run(self):
        from kit import cli
        for device in ({"mode": "normal", "pid": 0x1290}, {"mode": "dfu", "pid": 0x1227}):
            with self.subTest(device=device), patch.object(cli.R, "usb_inventory", return_value=[device]), \
                 patch.object(M, "list_devices", return_value=[]), patch.object(M, "query_service") as query, \
                 contextlib.redirect_stdout(io.StringIO()):
                result = cli.main(["transport-info", "--json", "--probe-service"])
            self.assertEqual(result, 2)
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
