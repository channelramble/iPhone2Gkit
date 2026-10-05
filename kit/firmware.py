"""Pinned stock iPhone1,1 firmware and conservative NAND compatibility rules.

NAND evidence comes from the actual RestoreKernelCaches in the pinned IPSWs.
The 8900 container uses plaintext format 4 in 1.0 and AES-CBC format 3 in
1.1.1/1.1.3 (public 0x837 key; zero IV). After LZSS decoding, the uncompressed
length and Adler32 were checked. Each __PRELINK/__info device-info-list entry
contains its 32-bit NAND read ID as a big-endian word at offset 8 of its data.

These tables establish the earliest supported target among our bundled stock
versions, not a guarantee that every restore/baseband/bootloader combination
will work. Serial numbers are never evidence of NAND compatibility.
"""

import copy
import re


_FIRMWARES = (
    {
        "version": "1.0", "build": "1A543a", "product_type": "iPhone1,1",
        "filename": "iPhone1,1_1.0_1A543a_Restore.ipsw", "size": 95604348,
        "sha256": "468d8b10061642b32f79046d6266a775cb26f02879839d8d445bdec82085c51a",
        "url": "https://dosdude1.com/files/iphone2gdowngrade/iPhone1,1_1.0_1A543a_Restore.ipsw",
    },
    {
        "version": "1.1.1", "build": "3A109a", "product_type": "iPhone1,1",
        "filename": "iPhone1,1_1.1.1_3A109a_Restore.ipsw", "size": 159668150,
        "sha256": "46c70ea61cc3a91a81a50b05b305ad5b2267519e1950da9700315406cf3f1861",
        "url": "https://secure-appldnld.apple.com/iPhone/061-3883.20070927.In76t/iPhone1,1_1.1.1_3A109a_Restore.ipsw",
    },
    {
        "version": "1.1.3", "build": "4A93", "product_type": "iPhone1,1",
        "filename": "iPhone1,1_1.1.3_4A93_Restore.ipsw", "size": 169950551,
        "sha256": "cf85531a365db28c5a839eaf6a5b6c53e49958bee3329aee53e60711e171215a",
        "url": "https://secure-appldnld.apple.com/iPhone/061-4061.20080115.4Fvn7/iPhone1,1_1.1.3_4A93_Restore.ipsw",
    },
    {
        "version": "3.1.3", "build": "7E18", "product_type": "iPhone1,1",
        "filename": "iPhone1,1_3.1.3_7E18_Restore.ipsw", "size": 238319275,
        "sha256": "ae27cb853cb57ba679f576400bee71ffe689ccd55763383ec8b9c6f422874e4e",
        "url": "https://secure-appldnld.apple.com/iPhone/061-7481.20100202.4orot/iPhone1,1_3.1.3_7E18_Restore.ipsw",
    },
)

# Retain exact table membership, rather than folding ambiguous entries into a
# made-up minimum. Offsets below refer to the decoded Mach-O, not the container.
_NAND_TABLES = (
    {
        "version": "1.0", "driver": "AppleS5L8900XFMC-FMC",
        "component": "kernelcache.restore.release.s5l8900xrb",
        "kernel_sha256": "7fa800fbf396ba0476a50282928cb66f116f73f023b77c0c7d97db8c1c1eec45",
        "macho_sha256": "80784375a332af15dca55a3b494d55ade03c5cfca41fafbe2ecef1cd0871af31",
        "array_offset": 0x4F15CD,
        "ids": ["0x95D1D32C", "0x95C1D3AD", "0xA585D598", "0x2555D5EC", "0x9551D3EC"],
    },
    {
        "version": "1.0", "driver": "AppleS5L8900XADMFMC-FMC",
        "component": "kernelcache.restore.release.s5l8900xrb",
        "kernel_sha256": "7fa800fbf396ba0476a50282928cb66f116f73f023b77c0c7d97db8c1c1eec45",
        "macho_sha256": "80784375a332af15dca55a3b494d55ade03c5cfca41fafbe2ecef1cd0871af31",
        "array_offset": 0x4F1A8A,
        "ids": ["0x95D1D32C", "0x95C1D3AD", "0xA585D598", "0x2555D5EC", "0x9551D3EC",
                "0xA514D3AD", "0xA555D5AD"],
        "qualification": "Driver selection depends on nand-enable-adm and board properties; the extra Hynix IDs do not prove a stock 1.0 minimum.",
    },
    {
        "version": "1.1.1", "driver": "AppleS5L8900XADMFMC-FMC",
        "component": "kernelcache.release.s5l8900xrb",
        "kernel_sha256": "b5f546a065b431a39cb15c7be73b26be0f468457c1e30caae3e6d0715a94876e",
        "macho_sha256": "f11a04dec2d6905f467c89a0774067ae5ba103ad59afa66212f397dc0df59907",
        "array_offset": 0x543031,
        "ids": ["0xB655D7EC", "0x2555D5EC", "0xB614D5EC", "0xA585D598",
                "0xBA94D598", "0xBA95D798", "0xA514D3AD", "0xA555D5AD", "0x3ED5D789"],
    },
    {
        "version": "1.1.3", "driver": "AppleS5L8900XADMFMC-FMC",
        "component": "kernelcache.release.s5l8900xrb",
        "kernel_sha256": "63c5b9131073c4d7038b899e227693c9720956e361c6dbe9ffc226e9512ad69e",
        "macho_sha256": "a9e5d0f141adb19bef39751327971d4de3d7c98d252702549fb90ab5efda6e5f",
        "array_offset": 0x54D4ED,
        "ids": ["0x3E94D589", "0x3E94D52C", "0x3ED5D72C", "0xB655D7EC",
                "0x2555D5EC", "0xB614D5EC", "0xA585D598", "0xBA94D598",
                "0xBA95D798", "0xA514D3AD", "0xA555D5AD", "0x3ED5D789"],
    },
)
_ONE_ZERO_IDS = frozenset(int(n, 16) for n in _NAND_TABLES[0]["ids"])
_AMBIGUOUS_IDS = frozenset((0xA514D3AD, 0xA555D5AD))
_ONE_ONE_ONE_IDS = frozenset(int(n, 16) for n in _NAND_TABLES[2]["ids"])
_ONE_ONE_THREE_IDS = frozenset(int(n, 16) for n in _NAND_TABLES[3]["ids"])


def firmwares():
    """Return a JSON-friendly catalog; callers may safely annotate the copies."""
    return [dict(record) for record in _FIRMWARES]


def nand_tables():
    """Return the extracted table evidence, including the conditional 1.0 table."""
    return copy.deepcopy(list(_NAND_TABLES))


def _nand_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"0[xX][0-9a-fA-F]{1,8}", value):
            number = int(value, 16)
        elif re.fullmatch(r"[0-9a-fA-F]{8}", value) and re.search(r"[a-fA-F]", value):
            number = int(value, 16)
        elif re.fullmatch(r"[0-9]{1,10}", value):
            number = int(value, 10)
        else:
            return None
    else:
        return None
    return number if 0 < number <= 0xFFFFFFFF else None


def recommended_target(product_type="iPhone1,1", serial=None, capacity_gb=None, nand_id=None):
    """Choose a proven NAND-table minimum; never infer one from a serial/date.

    ``eligibility`` describes eligibility for stock 1.0, while ``target`` is the
    earliest confirmed bundled target. An unknown result always has target=None.
    A capacity alone does not identify
    the NAND. The 16 GB model cannot run stock 1.0, but that fact alone does not
    distinguish 1.1.1 from 1.1.3. ``serial`` is deliberately informational only.
    """
    if product_type != "iPhone1,1":
        return {"target": None, "eligibility": "ineligible",
                "reason": "Only the original iPhone (iPhone1,1) is supported."}
    chip = _nand_id(nand_id)
    if chip is None:
        reason = "NAND compatibility is unknown; a serial number or capacity cannot identify the earliest supported firmware."
        if capacity_gb == 16:
            reason += " The 16 GB iPhone cannot run stock 1.0; its earliest target still requires a known NAND ID."
        elif nand_id is not None:
            reason += " Supply a valid 32-bit NAND read ID."
        return {"target": None, "eligibility": "unknown", "reason": reason}
    label = "0x%08X" % chip
    if chip in _AMBIGUOUS_IDS:
        return {"target": None, "eligibility": "unknown", "reason":
                "%s appears in the conditional 1.0 ADM driver and in 1.1.1. Driver selection is not established, so the earliest stock target is unknown." % label}
    if chip in _ONE_ZERO_IDS:
        if capacity_gb == 16:
            return {"target": None, "eligibility": "unknown", "reason":
                    "%s is in the 1.0 NAND table, but the reported 16 GB model cannot run stock 1.0. Resolve this conflicting information before choosing an automatic target." % label}
        target = "1.0"
    elif chip in _ONE_ONE_ONE_IDS:
        target = "1.1.1"
    elif chip in _ONE_ONE_THREE_IDS:
        target = "1.1.3"
    else:
        return {"target": None, "eligibility": "unknown", "reason":
                "%s is absent from the verified 1.x NAND tables; no earliest target is proven." % label}
    if target != "1.0":
        return {"target": target, "eligibility": "ineligible", "reason":
                "%s is absent from both 1.0 kernel NAND tables, so stock 1.0 cannot boot this NAND. It is supported by %s, the earliest confirmed bundled target for this ID. Serial numbers are not used." % (label, target)}
    return {"target": target, "eligibility": "eligible", "reason":
            "%s is supported by the 1.0 kernel NAND table. Serial numbers are not used." % label}


def one_zero_warning():
    return ("Stock iPhone OS 1.0 supports a limited set of NAND chips and cannot run "
            "on the 16 GB iPhone. Serial numbers and manufacturing dates do not prove "
            "compatibility. Use a verified NAND read ID; an unknown result requires "
            "a manual target choice and does not mean that 1.0 is safe.")
