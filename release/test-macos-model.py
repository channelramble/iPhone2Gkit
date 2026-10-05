#!/usr/bin/env python3
"""Run the actual Swift setup model against a fake engine, without a phone."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


FAKE_ENGINE = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["MODEL_TEST_COMMANDS"], "a") as log:
    log.write(json.dumps(args) + "\n")
if "setup" in args:
    scenario = os.environ["MODEL_TEST_SCENARIO"]
    Path(os.environ["MODEL_TEST_PID"]).write_text(str(os.getpid()))
    if scenario == "cancel":
        try: time.sleep(120)
        except KeyboardInterrupt: sys.exit(130)
    if scenario == "failure":
        print("error: simulated interrupted download")
        sys.exit(1)
    print("@@" + json.dumps({"event":"download", "version":"kit", "pct":100}))
    print(json.dumps({"ready":scenario == "success", "verified":scenario == "success",
                      "kit":"/managed-kit", "apps":48, "resource_version":"1"}))
elif "doctor" in args:
    bad = "--kit" in args and args[args.index("--kit") + 1] == "/old-invalid-kit"
    print(json.dumps({"ok":not bad, "problems":["invalid custom kit"] if bad else [],
                      "kit":"/old-invalid-kit" if bad else "/managed-kit", "phone":None,
                      "irecovery":"1.3.1", "setup":{"ready":not bad}}))
    sys.exit(1 if bad else 0)
elif "list" in args:
    print("[]")
elif "restore-info" in args:
    print("{}")
else:
    print("Unexpected phone action: " + repr(args))
    sys.exit(99)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    root = args.source.resolve()
    swift = root / "macos/Sources/iOS1Kit"
    harness = Path(__file__).with_suffix(".swift").resolve()
    with tempfile.TemporaryDirectory(prefix="iphone2gkit-model-check-") as temporary:
        work = Path(temporary)
        executable = work / "iphone2gkit-model-check"
        subprocess.run(["swiftc", "-parse-as-library", str(swift / "Engine.swift"),
                        str(swift / "Model.swift"), str(harness), "-o", str(executable)],
                       check=True, timeout=120)
        fake = work / "fake-engine.py"
        fake.write_text(FAKE_ENGINE)
        failed = []
        for scenario in ("success", "failure", "invalid", "cancel"):
            commands = work / (scenario + ".jsonl")
            env = dict(os.environ, IOS1KIT_ENGINE=str(fake), IOS1KIT_PYTHON=str(args.python.resolve()),
                       MODEL_TEST_SCENARIO=scenario, MODEL_TEST_PID=str(work / (scenario + ".pid")),
                       MODEL_TEST_COMMANDS=str(commands))
            result = subprocess.run([str(executable)], env=env, timeout=20)
            if result.returncode:
                failed.append(scenario)
            calls = [json.loads(line) for line in commands.read_text().splitlines()]
            assert all("probe" not in call for call in calls), "Concurrent phone action started during setup"
        assert not failed, "Mac setup checks failed: " + ", ".join(failed)
    print("All four Mac setup model checks passed; no phone actions.")


if __name__ == "__main__":
    main()
