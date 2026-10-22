#!/usr/bin/env python3
"""Inject a library into selected launchd jobs (SWEET bring-up).

  patch-launchd-inject.py IN.plist OUT.plist LABEL=LIBRARY [LABEL=LIBRARY ...]

IN is /System/Library/xpc/launchd.plist (binary plist, read by launchd when
booted with launchd_unsecure_cache=1).  For each job the library is added to
EnvironmentVariables.DYLD_INSERT_LIBRARIES, so the program is started with the
library loaded before anything else.

This is how the userland gets a renderer: the window server (backboardd) is
started with /redwood/lib/libredwood-gl.dylib, whose constructor replaces the
EAGLContext methods CoreAnimation asks for and hands the drawing to Mesa's
OSMesa (softpipe) - no GPU driver needed.

A library already in the list is not added twice, so the tool can be re-run.
Positional argument values are plist strings; ':' in a library path is fine.
"""
import plistlib
import sys

KEY = 'DYLD_INSERT_LIBRARIES'


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    wanted = {}
    for spec in sys.argv[3:]:
        if '=' not in spec:
            sys.exit(f'expected LABEL=LIBRARY, got {spec!r}')
        label, library = spec.split('=', 1)
        wanted.setdefault(label, []).append(library)

    with open(src, 'rb') as f:
        plist = plistlib.load(f)

    found = set()
    for job in plist.get('LaunchDaemons', {}).values():
        label = job.get('Label')
        if label not in wanted:
            continue
        env = job.setdefault('EnvironmentVariables', {})
        have = [p for p in env.get(KEY, '').split(':') if p]
        for library in wanted[label]:
            if library not in have:
                have.append(library)
        env[KEY] = ':'.join(have)
        found.add(label)

    missing = set(wanted) - found
    if missing:
        sys.exit(f'jobs not found: {", ".join(sorted(missing))}')

    with open(dst, 'wb') as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)
    for label in sorted(found):
        print(f'{dst}: {label} gets {KEY}={":".join(wanted[label])}')


if __name__ == '__main__':
    main()
