#!/bin/bash
# Run the shim under QEMU: validates shim + loader logic without a phone.
#
#   perseus/qemu-shim.sh            # both loader paths (ramdisk and behind _end)
#   perseus/qemu-shim.sh ramdisk    # only the /chosen/linux,initrd path
#   perseus/qemu-shim.sh behind     # only the payload-after-_end path
#
# The shim is built with -DPERSEUS_QEMU, which mirrors the framebuffer console
# to QEMU's PL011 and skips the DSI/UFS probes (that hardware does not exist
# on the virt board). Everything else is the same code that runs on the phone.
set -euo pipefail
cd "$(dirname "$0")/.."
KERNEL=${KERNEL:-out/kernel.macho}
QEMU=${QEMU:-qemu-system-aarch64}
CASE=${1:-both}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

[[ -f $KERNEL ]] || { echo "no kernel at $KERNEL (set KERNEL=)" >&2; exit 1; }
command -v "$QEMU" >/dev/null || { echo "no $QEMU (set QEMU=)" >&2; exit 1; }

echo "== shim (-DPERSEUS_QEMU) =="
: > "$TMP/dummy.bin"
EXTRA_CFLAGS=-DPERSEUS_QEMU bash shim/build.sh "$TMP/dummy.bin" >/dev/null

echo "== payload (QEMU device tree) =="
python3 tools/mkadt.py "$TMP/qemu.adt" --qemu
python3 tools/mkpayload.py "$KERNEL" "$TMP/qemu.adt" "$TMP/payload.bin" \
    --cmdline 'debug=0xc serial=3 cpus=1'

run() {
    local what=$1; shift
    echo "== $what =="
    timeout 40 "$QEMU" -M virt,gic-version=3 -cpu max -m 4096 -nographic "$@" 2>&1 | head -12
}

if [[ $CASE == ramdisk || $CASE == both ]]; then
    run "ramdisk path (ABL-style -initrd)" -kernel shim/out/Image -initrd "$TMP/payload.bin"
fi
if [[ $CASE == behind || $CASE == both ]]; then
    cat shim/out/Image "$TMP/payload.bin" > "$TMP/shim-with-payload.bin"
    run "payload behind _end (topaz layout)" -kernel "$TMP/shim-with-payload.bin"
fi
