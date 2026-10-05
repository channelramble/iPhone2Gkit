"""Payload catalog and translation of iLiberty payload scripts into ramdisk steps.

The kit's iLiberty payloads (`NNiOS1Foo.sh` + `iOS1-Foo.zip`) were written to run
inside the booted phone (pass 2), with "/" being the phone's root. The ramdisk
installer instead runs with the phone's system partition at $R (/mnt1) and the
data partition at $R/private/var, so every absolute path gets the $R prefix.

Only a small, known vocabulary is accepted. Anything else makes the build fail,
so an unexpected script can never run half-translated on the phone.
"""
import json
import os
import re
import shlex
import zipfile

# Packs that are not offered: the iLiberty "master" bundles duplicate the
# individual packs (and are what broke under iLiberty).
MASTER_PACKS = {"iOS1-AllApps.zip", "iOS1-AppsOnly.zip"}
APP_CATS = {"Utility", "Game", "Reading", "Network", "Advanced"}
# Files post-steps may touch that come from the phone (or ios1kit) rather than the pack.
DEVICE_FILES = {
    "System/Library/LaunchDaemons/com.apple.SpringBoard.plist",
    "private/etc/ssh_host_rsa_key",          # installed by ios1kit with OpenSSH
}


class PayloadError(RuntimeError):
    pass


def _q(path):
    """Quote a device path under $R for the generated shell script."""
    if not path.startswith("/"):
        raise PayloadError("relative path in payload script: %r" % path)
    return '"$R%s"' % path.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


# Lines that belong to iLiberty's wrapper and are replaced by the installer itself.
_SKIP = [
    r"#.*", r"", r"\. \$FUNCTIONS", r'echo .*', r"PACK=[\w.-]+",
    r"run unzip -o \$\{PL_DIR\}/\$\{PACK\}\.zip -d /", r"rc=\$\?",
    r"fi",
    r"rm -f \$\{PL_DIR\}/\$\{PACK\}\.zip",
]
_SKIP_RE = [re.compile(r"^\s*%s\s*$" % p) for p in _SKIP]


def translate_script(text):
    """Return the list of post-unzip shell lines (with $R prefixes) for one payload script."""
    out = []
    in_unzip_check = False
    for raw in text.splitlines():
        line = raw.strip()
        if re.match(r"^if \[ \$rc -gt 1 \]; then$", line):
            in_unzip_check = True
            continue
        if in_unzip_check:
            if line == "fi":
                in_unzip_check = False
            continue
        if any(r.match(line) for r in _SKIP_RE):
            if line == "fi":
                out.append("fi")
            continue
        try:
            argv = shlex.split(line)
        except ValueError as e:
            raise PayloadError("cannot parse %r: %s" % (line, e))
        cmd = argv[0]
        if cmd == "chmod" and len(argv) == 3 and re.fullmatch(r"[0-7]{3,4}", argv[1]):
            out.append("chmod %s %s" % (argv[1], _q(argv[2])))
        elif cmd == "mkdir" and len(argv) >= 3 and argv[1] == "-p":
            out.append("mkdir -p " + " ".join(_q(p) for p in argv[2:]))
        elif cmd == "ln" and len(argv) == 4 and argv[1] == "-sf":
            # the link target is resolved on the phone at run time, so it stays unprefixed
            out.append("rm -f %s" % _q(argv[3]))
            out.append("ln -s %s %s" % (shlex.quote(argv[2]), _q(argv[3])))
        elif cmd == "cp" and len(argv) == 3:
            out.append("cp %s %s" % (_q(argv[1]), _q(argv[2])))
        elif re.fullmatch(r"if \[ ! -f (\S+) \]; then", line):
            out.append("if [ ! -f %s ]; then" % _q(re.fullmatch(r"if \[ ! -f (\S+) \]; then", line).group(1)))
        else:
            raise PayloadError("unsupported command in payload script: %r" % line)
    if out.count("fi") != sum(1 for l in out if l.startswith("if ")):
        raise PayloadError("unbalanced if/fi in payload script")
    return out


class Payload:
    def __init__(self, entry, folder):
        self.entry = entry
        self.name = entry["name"]
        self.cat = entry["cat"]
        self.script_path = os.path.join(folder, entry["script"])
        self.zip_path = os.path.join(folder, entry["pack"])
        self.pack = entry["pack"]
        self.sys_kb = int(entry.get("sys_kb", 0))
        self.var_kb = int(entry.get("var_kb", 0))
        self.key = re.sub(r"[^a-z0-9]", "", os.path.splitext(entry["pack"])[0].lower().replace("ios1-", ""))

    @property
    def zip_size(self):
        return os.path.getsize(self.zip_path)

    def era(self):
        from . import provenance
        return provenance.describe(self.entry, self.zip_path)

    def post_lines(self):
        with open(self.script_path, encoding="utf-8", errors="replace") as f:
            return translate_script(f.read())

    def check(self):
        for p in (self.script_path, self.zip_path):
            if not os.path.isfile(p):
                raise PayloadError("missing payload file: %s" % p)
        with zipfile.ZipFile(self.zip_path) as z:
            bad = z.testzip()
            if bad:
                raise PayloadError("%s is corrupt (first bad member: %s)" % (self.pack, bad))
            names = set(z.namelist())
            for n in names:
                if n.startswith("/") or ".." in n.split("/"):
                    raise PayloadError("%s contains unsafe path %r" % (self.pack, n))
        # Post-steps are checked on the phone, so a chmod/cp of a file that will
        # not exist would abort the install: catch it here instead.
        for line in self.post_lines():
            m = re.match(r'(?:chmod [0-7]+|cp) "\$R/((?:[^"\\]|\\.)*)"', line)
            if m:
                path = re.sub(r"\\(.)", r"\1", m.group(1))
                if path not in names and path not in DEVICE_FILES:
                    raise PayloadError("%s: post-step touches %s, which is not in the pack" % (self.pack, path))


def load_catalog(apps_dir):
    folder = os.path.join(apps_dir, "iliberty-payloads")
    with open(os.path.join(apps_dir, "catalog.json"), encoding="utf-8") as f:
        entries = json.load(f)
    return [Payload(e, folder) for e in entries if e["pack"] not in MASTER_PACKS]


def select(catalog, spec):
    """spec: 'none', 'apps' (every non-tweak), 'all' (apps + safe tweaks), or comma list of keys/names."""
    spec = (spec or "none").strip()
    if spec == "none":
        return []
    if spec == "apps":
        return [p for p in catalog if p.cat in APP_CATS]
    if spec == "all":
        # SummerBoard replaces SpringBoard's launch plist; keep it opt-in only.
        return [p for p in catalog if p.key not in ("summerboard", "undosummerboard")]
    chosen = []
    for item in [s.strip() for s in spec.split(",") if s.strip()]:
        norm = re.sub(r"[^a-z0-9]", "", item.lower())
        hits = [p for p in catalog if p.key == norm or re.sub(r"[^a-z0-9]", "", p.name.lower()) == norm]
        if not hits:
            raise PayloadError("unknown app %r (see `ios1kit list`)" % item)
        for h in hits:
            if h not in chosen:
                chosen.append(h)
    keys = {p.key for p in chosen}
    if "summerboard" in keys and "undosummerboard" in keys:
        raise PayloadError("choose either SummerBoard or Undo SummerBoard, not both")
    # Launcher first: 1.0's home screen only shows ~4 extra icons without it.
    chosen.sort(key=lambda p: (p.key != "launcher", catalog.index(p)))
    return chosen
