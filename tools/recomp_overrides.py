"""Native-override routing for the emitter: where the entry points come from.

The override entry points are declared in C -- x86_register_override(0x..., fn)
in the subsystem files under src/native/ -- and recomp.py routes every call to
one of them through the dispatcher so the runtime's override slot can pick the
native implementation. The C is the single source of truth: no JSON, no
generator. This module is the one place the emitter learns that list.

It also owns the orphaned-chunk cleanup that a shrinking module exposes: an
earlier emit's chunk files that the current emit did not produce would
otherwise linger in src/recomp/*.c, get globbed by the build, and compile a
stale copy with a STALE translator stamp. That is how a no-isolate re-emit left
the isolated-function chunks of an isolate emit behind (see the emit command).
"""

import hashlib
import os
import re


def scan_overrides(root):
    """The override registrations, read from the register_override calls in the
    hand-written host C. Returns a list of (module, linked_ep, file, line).

    Each registration names the MODULE that owns the entry point, because a
    bare address is not a key here: every libIG*.dll is linked for 0x10000000,
    so one linked address is a real function in eight of this game's modules.
    Matching on the address alone routed calls through the dispatcher in every
    one of them and let an override intended for a relocated module fire for
    whichever module kept the preferred base."""
    native = os.path.join(root, "src", "native")
    if not os.path.isdir(native):
        raise SystemExit(
            "recomp: %s does not exist -- cannot scan for "
            "x86_register_override calls. Refusing rather than emitting a "
            "module whose native overrides would never fire.\n"
            "  The translator is SHARED and resolves the port from the working "
            "directory, so run it from the port's root, not from the "
            "translator's." % native)
    pat = re.compile(r'x86_register_override\(\s*"([^"]+)"\s*,\s*'
                     r'0x([0-9a-fA-F]+)')
    bare = re.compile(r'x86_register_override\(\s*0x')
    out = []
    for fn in sorted(os.listdir(native)):
        if not fn.endswith(".c"):
            continue
        path = os.path.join(native, fn)
        with open(path) as f:
            text = f.read()
        if bare.search(text):
            raise SystemExit(
                "recomp: %s calls x86_register_override with a bare address "
                "and no module name. One linked address names a different "
                "function in every module linked for the same base, so the "
                "emitter cannot tell which one to route." % path)
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append((m.group(1), int(m.group(2), 16), path, line))
    return out


def overrides_for_module(root, program, known_eps):
    """The override entry points belonging to `program` (the module being
    emitted), as a set. Fails loudly when a registration names this module but
    an entry point it does not have: an override on a function that was never
    emitted cannot fire, and the build would succeed anyway."""
    mine, missing = set(), []
    for module, ep, path, line in scan_overrides(root):
        if module != program:
            continue
        if ep in known_eps:
            mine.add(ep)
        else:
            missing.append((ep, path, line))
    if missing:
        raise SystemExit(
            "recomp: %d override(s) name module %s at an entry point it does "
            "not have: %s. An override on a function this module never emits "
            "cannot fire, and the link would succeed anyway."
            % (len(missing), program,
               ", ".join("0x%08x (%s:%d)" % (ep, p, l)
                         for ep, p, l in missing)))
    return mine


def emitted_fingerprint(translator_path, root, program, routing_path=__file__):
    """Hash every input that changes override routing in emitted C.

    Native implementation edits do not require a re-emit, but adding or
    removing a (module, entry-point) registration does: direct calls to that
    entry point change between a plain C call and DISPATCH. Paths and line
    numbers are intentionally excluded because moving the same registration
    within src/native does not change generated semantics.
    """
    stem = program.rsplit(".", 1)[0].lower()
    registrations = sorted({(module, ep)
                            for module, ep, _, _ in scan_overrides(root)
                            if module.rsplit(".", 1)[0].lower() == stem})
    digest = hashlib.sha256()
    with open(translator_path, "rb") as source:
        digest.update(source.read())
    digest.update(b"\0override-router-v1\0")
    with open(routing_path, "rb") as source:
        digest.update(source.read())
    digest.update(b"\0native-overrides-v1\0")
    for module, ep in registrations:
        digest.update(module.encode("utf-8"))
        digest.update(b"\0")
        digest.update(("%08x" % ep).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def remove_orphan_chunks(stem, nchunk):
    """Delete chunk files an earlier emit wrote past this emit's count.

    The build globs src/recomp/*.c and compiles every chunk it finds, so an
    orphaned chunk from a PREVIOUS emit is not skipped -- it compiles a stale
    copy with a stale translator stamp and, for an overridden function, a call
    routed to the recompiled body instead of the override. Returns the number
    removed (0 is printed as a count, not silence)."""
    import glob
    removed = 0
    for old in sorted(glob.glob(stem + "_[0-9][0-9][0-9].c")):
        m = re.match(r".*?_(\d{3})\.c$", old)
        if m and int(m.group(1)) >= nchunk:
            os.remove(old)
            removed += 1
    return removed


def _selftest():
    """Prove both gates REJECT, not just that they accept.

    Every registration in the tree is well-formed, so the accepting path runs
    on every emit and the refusing paths never do. This builds a throwaway tree
    for each way a registration can be wrong and fails if the gate lets it
    through. Run as `python3 tools/recomp_overrides.py --selftest`; needs
    neither Ghidra nor the game.
    """
    import shutil
    import tempfile

    def tree(body):
        d = tempfile.mkdtemp(prefix="ovsel")
        os.makedirs(os.path.join(d, "src", "native"))
        with open(os.path.join(d, "src", "native", "t.c"), "w") as f:
            f.write(body)
        return d

    fails = 0
    checks = []

    # REJECT: a bare address names a different function in every module
    # linked for the same base, so the emitter cannot route it.
    d = tree('x86_register_override(0x10002520, f);\n')
    try:
        scan_overrides(d)
        checks.append(("bare address with no module", False,
                       "accepted -- the emitter would route it in every "
                       "module that has that address"))
    except SystemExit as e:
        checks.append(("bare address with no module", True, str(e)))
    shutil.rmtree(d)

    # The emitted stamp must move when the authoritative registration SET
    # changes. Before this check existed, check_emitted declared generated C
    # current after a new override was added even though its direct callers
    # still bypassed the runtime override slot.
    d = tree('x86_register_override("XMen2.exe", 0x00617480, f);\n')
    baseline = emitted_fingerprint(__file__, d, "XMen2.exe")
    with open(os.path.join(d, "src", "native", "t.c"), "a") as f:
        f.write('x86_register_override("XMen2.exe", 0x005604f0, g);\n')
    changed = emitted_fingerprint(__file__, d, "XMen2.exe")
    ok = baseline != changed
    checks.append(("override-set fingerprint changes", ok,
                   "%s -> %s" % (baseline, changed)))
    shutil.rmtree(d)

    # Moving the same set between native files is not an emitted-code change;
    # including paths/lines would create false staleness and needless re-emits.
    d = tree('x86_register_override("XMen2.exe", 0x00617480, f);\n')
    baseline = emitted_fingerprint(__file__, d, "XMen2.exe")
    os.rename(os.path.join(d, "src", "native", "t.c"),
              os.path.join(d, "src", "native", "moved.c"))
    moved = emitted_fingerprint(__file__, d, "XMen2.exe")
    ok = baseline == moved
    checks.append(("same set moved between files", ok,
                   "%s -> %s" % (baseline, moved)))
    shutil.rmtree(d)

    # REJECT: a registration naming a module at an entry point it does not
    # have. The override could never fire and the link would still succeed.
    d = tree('x86_register_override("XMen2.exe", 0x00617481, f);\n')
    try:
        overrides_for_module(d, "XMen2.exe", {0x00617480})
        checks.append(("entry point the module lacks", False,
                       "accepted -- an override on a function that is never "
                       "emitted cannot fire"))
    except SystemExit as e:
        checks.append(("entry point the module lacks", True, str(e)))
    shutil.rmtree(d)

    # REFUSE: no src/native at all. "Found nothing" and "never looked" must
    # not produce the same answer.
    d = tempfile.mkdtemp(prefix="ovsel")
    try:
        scan_overrides(d)
        checks.append(("missing src/native", False,
                       "returned an empty list instead of refusing"))
    except SystemExit as e:
        checks.append(("missing src/native", True, str(e)))
    shutil.rmtree(d)

    # ACCEPT: a well-formed registration must survive both gates, or the
    # gates are simply refusing everything.
    d = tree('x86_register_override("XMen2.exe", 0x00617480, f);\n')
    got = scan_overrides(d)
    mine = overrides_for_module(d, "XMen2.exe", {0x00617480})
    ok = (len(got) == 1 and got[0][0] == "XMen2.exe"
          and got[0][1] == 0x00617480 and mine == {0x00617480})
    checks.append(("a well-formed registration", ok,
                   "scanned %r -> claimed %s" % (got, sorted(mine))))
    shutil.rmtree(d)

    # ACCEPT: a registration for ANOTHER module must not be claimed by this
    # one. That silent cross-claim is the defect this key exists to prevent.
    d = tree('x86_register_override("libCriMovie.dll", 0x10002520, f);\n')
    mine = overrides_for_module(d, "cgD3D8.dll", {0x10002520})
    ok = (mine == set())
    checks.append(("another module's registration", ok,
                   "cgD3D8.dll claimed %s (must be none: the address is a "
                   "real function in both)" % sorted(mine)))
    shutil.rmtree(d)

    for name, ok, detail in checks:
        print("  %-32s %s" % (name, "ok" if ok else "FAIL"))
        if not ok:
            print("      %s" % detail)
            fails += 1
    print("recomp_overrides --selftest: %s (%d of %d case(s) failed)"
          % ("FAILED" if fails else "PASSED", fails, len(checks)))
    return fails


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(_selftest())
    sys.exit("usage: recomp_overrides.py --selftest")
