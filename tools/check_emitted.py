#!/usr/bin/env python3
"""Refuse a build whose recompiled C is older than the translator that makes it.

    tools/check_emitted.py            report, exit non-zero if anything is stale
    tools/check_emitted.py --list     one line per module, always exit 0
    tools/check_emitted.py --selftest prove it detects a stale stamp

WHY THIS EXISTS. src/recomp/*.c is generated and gitignored, so nothing that is
tracked ever shows that a module has fallen behind tools/recomp.py. A stale
module compiles, links, runs, and is WRONG in exactly the way the fix it missed
was about to correct -- there is no error, no warning, and no diff to notice.

It cost days. libIGSg was last emitted before two translator fixes landed: a
tail-call ABI change touching 209 sites in that module alone, and a reversed
x87 FSUBR/FDIVR at three more. The port drew warped characters the whole time
(issue #80), and every instrument built to find it was aimed at source code
that was already correct -- the defect was in a build artifact nobody had
regenerated. It came right the moment the module was re-emitted for an
unrelated reason, which is the worst way to learn any of this.

The stamp is a CONTENT hash of recomp.py, the override-routing scanner and the
generated module's scanned native-override registration set, not a git hash:
any uncommitted input changes what it emits just as much as a commit does. A
file with no stamp at all was emitted before stamping existed and is therefore
stale by definition -- it is reported as stale, never skipped.
"""
import glob
import os
import re
import sys

from recomp_overrides import emitted_fingerprint

# TWO roots, and they are not the same one. The translator is SHARED -- it
# lives in `recomp-x86` and serves every x86 port -- while the emitted C being
# checked belongs to the PORT. Resolving both from __file__ made this look for
# the port's generated code inside the translator's own repo and report that it
# held no emitted modules, which is a true statement about the wrong directory.
TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECOMP = os.path.join(TOOL_ROOT, "tools", "recomp.py")


def project_root(argv):
    """The PORT being checked -- told, not guessed.

    Both guesses are wrong somewhere. Resolving from __file__ points at this
    translator's own repo, which holds no emitted modules, so the check reports
    "nothing to check" and passes having looked at nothing. Resolving from the
    working directory is right when a human runs it from the port and wrong
    when the build system runs it from the build directory, which is where it
    actually matters. So callers pass --root, and CMake passes its source dir.
    """
    for i, a in enumerate(argv):
        if a == "--root":
            return os.path.abspath(argv[i + 1])
        if a.startswith("--root="):
            return os.path.abspath(a.split("=", 1)[1])
    return os.getcwd()
STAMP = re.compile(r"/\* recomp-fingerprint: ([0-9a-f]+) \*/")


def fingerprint(path=RECOMP, root=None, program="Game"):
    return emitted_fingerprint(path, root or os.getcwd(), program)


def stamp_of(path):
    """The stamp in an emitted file, or None if it carries none."""
    with open(path, errors="replace") as f:
        for _ in range(8):                      # it is in the first few lines
            line = f.readline()
            if not line:
                break
            m = STAMP.search(line)
            if m:
                return m.group(1)
    return None


def modules(gen):
    """-> {module: [(path, stamp)]}, over the emitted chunks in `gen`."""
    out = {}
    for p in sorted(glob.glob(os.path.join(gen, "*.c"))):
        b = os.path.basename(p)
        m = re.match(r"(.+?)_(\d{3}|native)\.c$", b)
        if not m:
            continue
        out.setdefault(m.group(1), []).append((p, stamp_of(p)))
    return out


def main(argv):
    root = project_root(argv)
    GEN = os.path.join(root, "src", "recomp")
    mods = modules(GEN)
    if not mods:
        sys.stderr.write(
            "check_emitted: %s holds NO emitted modules. Nothing was checked --\n"
            "  that is not a pass -- pass --root <port> if this is the wrong tree.\n"
            "  Generate them (see the port's AGENTS.md) before building.\n"
            % GEN)
        return 2

    stale, ok = [], []
    for mod in sorted(mods):
        want = fingerprint(root=root, program=mod)
        got = set(s for _, s in mods[mod])
        if got == {want}:
            ok.append(mod)
        else:
            stale.append((mod, got, want))

    if "--list" in argv or stale:
        for mod in sorted(mods):
            want = fingerprint(root=root, program=mod)
            got = set(s for _, s in mods[mod])
            mark = "current" if got == {want} else "STALE"
            shown = ", ".join(sorted(g or "(unstamped)" for g in got))
            print("  %-14s %-8s %d chunk(s)  got %s, want %s"
                  % (mod, mark, len(mods[mod]), shown, want))
    if "--list" in argv:
        print("fingerprints include the translator plus each module's "
              "authoritative native-override set")
        return 0

    if not stale:
        print("check_emitted: %d module(s), all emitted by the current "
              "translator and per-module native-override set" % len(ok))
        return 0

    sys.stderr.write(
        "\ncheck_emitted: %d of %d module(s) were emitted by a DIFFERENT "
        "tools/recomp.py or native-override set.\n\n"
        "  These build and run and are wrong in whatever way the translator "
        "fixes they\n  missed were about to correct. Re-emit them:\n\n"
        % (len(stale), len(mods)))
    for mod, _, _ in stale:
        sys.stderr.write(
            "    python3 tools/recomp.py emit scratch/recomp/%s.json "
            "src/recomp/%s.c --split 1500 \\\n"
            "        --isolate scratch/recomp/%s.isolate\n" % (mod, mod, mod))
    sys.stderr.write("\n  and re-run tools/recomp.py native for each, then "
                     "rebuild.\n")
    return 1


def selftest():
    """The check must fire on a stale stamp AND pass on a current one. A
    checker only ever run against the good case is not a checker."""
    import tempfile
    ok = True
    d = tempfile.mkdtemp(dir=os.path.join(TOOL_ROOT, "scratch"))
    root = os.path.join(d, "port")
    os.makedirs(os.path.join(root, "src", "native"))
    with open(os.path.join(root, "src", "native", "base.c"), "w") as f:
        f.write('x86_register_override("Game.exe", 0x00401000, f);\n')
    good = os.path.join(d, "m_000.c")
    with open(good, "w") as f:
        f.write("/* generated */\n/* recomp-fingerprint: %s */\nint x;\n"
                % fingerprint(root=root, program="Game.exe"))
    if stamp_of(good) != fingerprint(root=root, program="Game.exe"):
        ok = False
        print("  FAIL  a current stamp was not read back")
    else:
        print("  pass  a current stamp is read back")

    bad = os.path.join(d, "n_000.c")
    with open(bad, "w") as f:
        f.write("/* generated */\n/* recomp-fingerprint: deadbeefdeadbeef */\n")
    if stamp_of(bad) == fingerprint(root=root, program="Game.exe"):
        ok = False
        print("  FAIL  a stale stamp read as current")
    else:
        print("  pass  a stale stamp is seen as stale (%s)" % stamp_of(bad))

    none = os.path.join(d, "o_000.c")
    with open(none, "w") as f:
        f.write("/* generated by something older */\nint y;\n")
    if stamp_of(none) is not None:
        ok = False
        print("  FAIL  an UNSTAMPED file produced a stamp")
    else:
        print("  pass  an unstamped file reads as None, which counts as stale")

    # And the fingerprint must MOVE when the translator changes, or none of
    # the above means anything.
    tweaked = os.path.join(d, "recomp_tweaked.py")
    with open(RECOMP, "rb") as f:
        body = f.read()
    with open(tweaked, "wb") as f:
        f.write(body + b"\n# a change\n")
    if fingerprint(tweaked, root, "Game.exe") == fingerprint(
            root=root, program="Game.exe"):
        ok = False
        print("  FAIL  editing the translator did not change the fingerprint")
    else:
        print("  pass  editing the translator changes the fingerprint")

    routing_tweaked = os.path.join(d, "overrides_tweaked.py")
    routing = os.path.join(TOOL_ROOT, "tools", "recomp_overrides.py")
    with open(routing, "rb") as f:
        body = f.read()
    with open(routing_tweaked, "wb") as f:
        f.write(body + b"\n# a routing change\n")
    baseline = emitted_fingerprint(RECOMP, root, "Game.exe", routing)
    changed = emitted_fingerprint(RECOMP, root, "Game.exe", routing_tweaked)
    if baseline == changed:
        ok = False
        print("  FAIL  editing override routing did not change the fingerprint")
    else:
        print("  pass  editing override routing changes the fingerprint")

    # And the fingerprint must MOVE when the PORT's authoritative override set
    # changes. This is independent of recomp.py's bytes, and was the missing
    # input that let stale direct calls bypass newly registered overrides.
    before = fingerprint(root=root, program="Game.exe")
    with open(os.path.join(root, "src", "native", "more.c"), "w") as f:
        f.write('x86_register_override("Game.exe", 0x00402000, g);\n')
    after = fingerprint(root=root, program="Game.exe")
    if before == after:
        ok = False
        print("  FAIL  changing the override set did not change the fingerprint")
    else:
        print("  pass  changing the override set changes the fingerprint")

    # A registration for another module cannot alter this module's generated
    # calls. Per-module stamps keep one override addition from forcing every
    # unrelated DLL in a large port to re-emit.
    before = fingerprint(root=root, program="Game.exe")
    with open(os.path.join(root, "src", "native", "other.c"), "w") as f:
        f.write('x86_register_override("Other.dll", 0x10001000, h);\n')
    after = fingerprint(root=root, program="Game.exe")
    if before != after:
        ok = False
        print("  FAIL  another module's override changed this fingerprint")
    else:
        print("  pass  another module's override leaves this fingerprint alone")

    import shutil
    shutil.rmtree(d)
    print("\nSELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main(sys.argv[1:]))
