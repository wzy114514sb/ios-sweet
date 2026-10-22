#!/bin/bash
# Put the software OpenGL ES stack into a perseus root image and point the window
# server at it.
#
#   redwood-image.sh IMAGE libOSMesa.8.dylib libredwood-gl.dylib
#
# The image is an HFS+ volume; Linux refuses a read-write mount of one that was
# not cleanly unmounted, so it is fsck'ed first.  Then:
#
#   /redwood/lib/libOSMesa.8.dylib    Mesa 25.0.7, softpipe (the CI "mesa" job)
#   /redwood/lib/libredwood-gl.dylib  HackiOS's EAGL shim over it (the same job)
#   /System/Library/xpc/launchd.plist backboardd starts with DYLD_INSERT_LIBRARIES
#
# Boot the result with launchd_unsecure_cache=1 so launchd reads that plist
# instead of its cached copy.
#
# Everything is idempotent: running it twice changes nothing the second time.
set -eu

if [ $# -ne 3 ]; then
  sed -n '2,19p' "$0"
  exit 1
fi

IMG=$1
OSMESA=$2
REDWOOD=$3
HERE=$(cd "$(dirname "$0")" && pwd)
MNT=$(mktemp -d)
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

echo "== fsck $IMG"
$SUDO fsck.hfsplus -f -y "$IMG" >/dev/null 2>&1 || true

echo "== mount $IMG"
$SUDO mount -t hfsplus -o rw,loop,force "$IMG" "$MNT"
trap 'sync; $SUDO umount "$MNT" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true' EXIT

echo "== libraries"
$SUDO mkdir -p "$MNT/redwood/lib"
$SUDO cp "$OSMESA" "$MNT/redwood/lib/libOSMesa.8.dylib"
$SUDO cp "$REDWOOD" "$MNT/redwood/lib/libredwood-gl.dylib"
$SUDO chown 0:0 "$MNT/redwood/lib" "$MNT/redwood/lib/libOSMesa.8.dylib" "$MNT/redwood/lib/libredwood-gl.dylib"
$SUDO chmod 0755 "$MNT/redwood" "$MNT/redwood/lib" "$MNT/redwood/lib/libOSMesa.8.dylib" "$MNT/redwood/lib/libredwood-gl.dylib"
$SUDO ls -l "$MNT/redwood/lib"

echo "== launchd.plist: inject into backboardd"
PLIST=$MNT/System/Library/xpc/launchd.plist
$SUDO python3 "$HERE/patch-launchd-inject.py" "$PLIST" "$PLIST.new" \
  'com.apple.backboardd=/redwood/lib/libredwood-gl.dylib'
$SUDO mv "$PLIST.new" "$PLIST"
$SUDO chown 0:80 "$PLIST"
$SUDO chmod 0644 "$PLIST"

echo "== verify"
$SUDO python3 - "$PLIST" <<'PY'
import plistlib, sys
p = plistlib.load(open(sys.argv[1], 'rb'))
for job in p['LaunchDaemons'].values():
    if job.get('Label') == 'com.apple.backboardd':
        print('backboardd env:', job.get('EnvironmentVariables'))
PY

sync
echo "== done; boot with launchd_unsecure_cache=1"
