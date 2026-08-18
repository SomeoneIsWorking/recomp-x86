#!/usr/bin/env python3
"""Run the translator's own tests and every tool selftest.

The translator is the component whose failures are quietest: it emits C that
compiles and links whatever it got wrong, so its checks are the only thing
standing between a mistranslated instruction and a port that runs and is wrong.

    python3 tests/run.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

UNIT = ["test_recomp.py", "test_recomp_hosted.py", "test_caselabel.py"]
SELFTESTS = [
    (["tools/recomp_overrides.py", "--selftest"],
     "override routing rejects a bare address and a wrong module"),
    (["tools/stackcheck.py", "--selftest"],
     "stack check flags a 4-byte drift and excludes tail callers"),
    (["tools/check_emitted.py", "--selftest"],
     "staleness stamp detects an edited translator"),
]


def main():
    fails = 0
    for t in UNIT:
        p = subprocess.run([sys.executable, os.path.join(HERE, t)],
                           cwd=ROOT, capture_output=True, text=True)
        print("  %-52s %s" % (t, "ok" if p.returncode == 0 else "FAIL"))
        if p.returncode:
            print((p.stdout + p.stderr)[-800:])
            fails += 1
    for cmd, what in SELFTESTS:
        p = subprocess.run([sys.executable] + cmd, cwd=ROOT,
                           capture_output=True, text=True)
        print("  %-52s %s" % (what, "ok" if p.returncode == 0 else "FAIL"))
        if p.returncode:
            print((p.stdout + p.stderr)[-800:])
            fails += 1
    # ghidra_export's own selftest is shell and needs no Ghidra.
    p = subprocess.run([os.path.join(ROOT, "tools", "ghidra_export.sh"),
                        "--selftest"], cwd=ROOT, capture_output=True, text=True)
    print("  %-52s %s" % ("lift step guard", "ok" if p.returncode == 0 else "FAIL"))
    fails += 1 if p.returncode else 0

    print("recomp-x86 tests: %s (%d failed)"
          % ("FAILED" if fails else "PASSED", fails))
    return fails


if __name__ == "__main__":
    sys.exit(main())
