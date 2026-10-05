"""Bounded, read-only queries to the host's existing USB multiplexing service.

Seeing a USB product ID does not establish that restored/lockdownd is reachable.
This module reports those separate facts. It never launches/stops a daemon,
pairs a phone, changes its mode, or sends StartRestore. Socket paths are fixed
in normal use; USBMUXD_SOCKET_ADDRESS and other environment overrides are ignored.
"""
import plistlib
import socket
import struct
import time
from xml.parsers.expat import ExpatError

SOCKET_PATH = "/var/run/usbmuxd"
MAX_REPLY = 1024 * 1024
HEADER = struct.Struct("<IIII")


class MuxError(RuntimeError):
    pass


def _timeout(sock, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise MuxError("USB connection service query timed out.")
    sock.settimeout(left)


def _read(sock, count, deadline):
    chunks = []
    while count:
        _timeout(sock, deadline)
        chunk = sock.recv(count)
        if not chunk:
            raise MuxError("USB connection service closed an incomplete reply.")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def _plist(data):
    try:
        value = plistlib.loads(data)
    except (ValueError, TypeError, OverflowError, plistlib.InvalidFileException, ExpatError) as e:
        raise MuxError("USB connection service returned an invalid plist.") from e
    if not isinstance(value, dict):
        raise MuxError("USB connection service returned a non-dictionary plist.")
    return value


def _request(sock, message, deadline):
    request = dict(message, ClientVersionString="iPhone2Gkit-2.0.1", ProgName="iPhone2Gkit",
                   kLibUSBMuxVersion=3)
    data = plistlib.dumps(request)
    _timeout(sock, deadline)
    sock.sendall(HEADER.pack(HEADER.size + len(data), 1, 8, 1) + data)
    length, version, kind, tag = HEADER.unpack(_read(sock, HEADER.size, deadline))
    if not HEADER.size < length <= MAX_REPLY or (version, kind, tag) != (1, 8, 1):
        raise MuxError("USB connection service returned an invalid message header.")
    return _plist(_read(sock, length - HEADER.size, deadline))


def _uint(value, limit):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limit


def _devices(reply):
    records = reply.get("DeviceList")
    if not isinstance(records, list) or len(records) > 256:
        raise MuxError("USB connection service did not return a valid device list.")
    devices, handles = [], set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("Properties"), dict):
            raise MuxError("USB connection service returned an invalid device record.")
        properties = record["Properties"]
        # Old daemon versions omit ConnectionType and enumerate only USB.
        if properties.get("ConnectionType", "USB") != "USB":
            continue
        handle = record.get("DeviceID")
        pid = properties.get("ProductID")
        if not _uint(handle, 0xFFFFFFFF) or not handle or not _uint(pid, 0xFFFF) or handle in handles:
            raise MuxError("USB connection service returned invalid or duplicate device identifiers.")
        handles.add(handle)
        serial = properties.get("SerialNumber")
        if serial is not None and (not isinstance(serial, str) or not 0 < len(serial) <= 128):
            raise MuxError("USB connection service returned an invalid device serial.")
        devices.append({"device_id": handle, "pid": pid, "usb_serial": serial})
    return devices


def list_devices(timeout=2, socket_path=SOCKET_PATH):
    """List USB devices without touching pairing records or phone services."""
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            _timeout(sock, deadline)
            sock.connect(socket_path)
            return _devices(_request(sock, {"MessageType": "ListDevices"}, deadline))
    except (OSError, TimeoutError) as e:
        raise MuxError("Cannot query Host USB connection service: %s" % e) from e


def query_service(device_id, timeout=3, socket_path=SOCKET_PATH):
    """Send QueryType only on port 62078, never a restore or pairing request.

    The phone's service uses big-endian plist lengths after the daemon's
    little-endian Connect reply. No caller-supplied port/command is accepted.
    """
    if not _uint(device_id, 0xFFFFFFFF) or not device_id:
        raise MuxError("Invalid USB device handle.")
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            _timeout(sock, deadline)
            sock.connect(socket_path)
            reply = _request(sock, {"MessageType": "Connect", "DeviceID": device_id,
                                   "PortNumber": socket.htons(62078)}, deadline)
            if reply.get("MessageType") != "Result" or type(reply.get("Number")) is not int or reply["Number"] != 0:
                raise MuxError("The phone's USB service is not accepting connections.")
            data = plistlib.dumps({"Request": "QueryType", "Label": "iPhone2Gkit"})
            _timeout(sock, deadline)
            sock.sendall(struct.pack(">I", len(data)) + data)
            length = struct.unpack(">I", _read(sock, 4, deadline))[0]
            if not 0 < length <= MAX_REPLY:
                raise MuxError("The phone returned an invalid service reply length.")
            result = _plist(_read(sock, length, deadline))
            service = result.get("Type")
            if service not in ("com.apple.mobile.lockdown", "com.apple.mobile.lockdownd", "com.apple.mobile.restored"):
                raise MuxError("The phone did not identify a supported USB service.")
            protocol = result.get("RestoreProtocolVersion")
            if service == "com.apple.mobile.restored" and not _uint(protocol, 0xFFFFFFFF):
                raise MuxError("The restore service did not report its protocol version.")
            return {"type": service, "protocol_version": protocol if service == "com.apple.mobile.restored" else None}
    except (OSError, TimeoutError) as e:
        raise MuxError("The phone's USB service query failed: %s" % e) from e


def assess(inventory, devices, error=None):
    """Separate physical detection, daemon visibility, and untested restore mode."""
    report = {"status": "unknown", "message": "USB transport could not be assessed.",
              "service_available": error is None, "visible": False, "device_id": None,
              "service_type": None, "protocol_version": None, "phone_query_error": None,
              "phone_count": len(inventory), "blocking": False,
              "detail": "This check does not prove a restore will complete. Restore-mode communication must be checked after its ramdisk boots."}
    if not inventory:
        report.update(status="no_phone", message="Connect the original iPhone by USB.", blocking=True)
    elif len(inventory) != 1:
        report.update(status="multiple_phones", message="Disconnect every other iPhone/iPod before checking or restoring.", blocking=True)
    elif error:
        report.update(status="service_unavailable", message=error, blocking=True)
    elif inventory[0].get("mode") in ("dfu", "recovery"):
        report.update(status="pending_restore_mode", message="Recovery/DFU detected. USB service communication can be checked once the restore ramdisk is running.")
    elif inventory[0].get("mode") != "normal" or inventory[0].get("pid") != 0x1290:
        report.update(status="unsupported_device", message="This USB mode is not confirmed as an original iPhone.", blocking=True)
    else:
        candidates = [d for d in devices if d["pid"] == inventory[0]["pid"]]
        serial = inventory[0].get("usb_serial")
        if serial:
            candidates = [d for d in candidates if d["usb_serial"] == serial]
        if len(candidates) == 1:
            report.update(status="visible", message="Host USB connection service lists the phone.",
                          visible=True, device_id=candidates[0]["device_id"])
        else:
            report.update(status="not_visible", message="The phone is physically connected, but Host USB connection service cannot identify it.",
                          detail="Normal-mode USB identity/SSH is unavailable through this service. This is not a cable-size problem. Recovery/DFU communication is separate; its restore-mode service still needs testing.")
    return report


def check(inventory, probe_service=False):
    try:
        devices, error = list_devices(), None
    except MuxError as e:
        devices, error = [], str(e)
    report = assess(inventory, devices, error)
    # Avoid talking to an arbitrary device when multiple phones are connected.
    if probe_service and report["visible"]:
        try:
            result = query_service(report["device_id"])
            report.update(service_type=result["type"], protocol_version=result["protocol_version"])
            if result["type"] == "com.apple.mobile.restored":
                report["message"] = "Restore service responds over USB (protocol %s)." % result["protocol_version"]
            else:
                report["message"] = "The phone's normal-mode service responds over USB."
        except MuxError as e:
            report["phone_query_error"] = str(e)
            report["message"] = "The host lists the phone, but its USB service did not answer."
    return report
