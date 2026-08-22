# recomp-x86

The **x86-32 → C static recompiler**: the translator, its Ghidra front end, and
the checks that stop it producing something that looks like progress.

It is shared because the instruction set is. The original Xbox is x86, not a
separate architecture, so a PC port and an OG Xbox port want *the same lifter*
with different loaders — and splitting them by console name is how three
separate x86 recompilers came to exist in this tree.

## What's here

```
tools/recomp.py             the translator. Subcommands:
                              report · emit (bodies) · runtime / native
                              (dispatch table) · dll (export shims + thunks)
tools/recomp_overrides.py   which entry points have a hand-written native
                            override, and the routing that reaches them
tools/recomp_hosted.py      the hosted (DLL-in-Wine) variant
tools/recomp_host_call.py   host-call plumbing
tools/check_emitted.py      is the generated C current with THIS translator?
tools/verify_export.py      does an export's block layout match the shipped PE?
tools/stackcheck.py         does every call return the esp its RET promises?
tools/ghidra_export.sh      PE -> functions.json, via headless Ghidra
tools/ghidra_scripts/       ExportFuncs, SeedPointerTables, MergeTruncated,
                            SplitFunction, caselabel
tools/seed_*.py             seeds for targets static analysis cannot reach
tools/pe.py                 PE container reader
```

## The rules it enforces on itself

These are not style preferences; each exists because its absence produced a
logged defect.

- **A translator that does not understand an instruction fails loudly by name.**
  Never a comment, a no-op, or best-effort code. Unhandled cases raise
  `Unsupported`, the function is recorded untranslatable with the reason, and
  `recomp.py report` ranks the reasons by how many functions each blocks. A
  recompiler that quietly skips instructions produces a binary that runs and is
  wrong.
- **Every stage refuses rather than producing something smaller that looks like
  progress**: a missing JSON, a zero-function export, an export whose block
  layout disagrees with the shipped PE.
- **The generated C carries an emitted-input content hash.** It covers the
  translator, the override-routing scanner, and that module's authoritative
  `(module, entry-point)` native override set: adding an override rewrites
  direct callers through `DISPATCH` even when `recomp.py` itself did not
  change, without making unrelated DLLs stale. Generated C is gitignored, so
  nothing tracked would otherwise show that it had fallen behind — and a stale
  module builds, links, runs, and is wrong in exactly the way the fix it missed
  was about to correct.

## It resolves the PORT from the working directory

Run it from the port's root, never from here. The translator is shared; the
port is not. Both `recomp.py` (scanning `src/native` for
`x86_register_override`) and `check_emitted.py` (finding `src/recomp`) resolve
the port from the working directory and **refuse, naming the trap**, if it does
not look like one.

That refusal is load-bearing. Resolved from `__file__` instead, `recomp.py`
would scan *this* repo for overrides, find none, and emit a module with every
native override silently absent — a build that compiles, links and runs.

## Test

```sh
python3 tests/run.py
```

## History

Extracted from the X-Men Legends II port (`pc/xmen2`) on 2026-08-18. Commit
history for these files before that date lives in that repository. The port's
own runtime — the dispatcher (`x86rt_native.c`), the guest-thread scheduler and
the PE/Win32 host — has **not** moved yet: the scheduler reaches into `pe_map`
and `winmm`, so that cut needs a dependency inversion rather than a file move.
