#!/bin/bash
# Build the whole perseus boot chain in one shot.
#
#   perseus/build.sh                    # -> out/perseus-64m.img
#   perseus/build.sh --no-pad           # keep the natural size
#
# Inputs it expects next to the repo:
#   research/ipsw-x/d22ap.adt   iPhone X /product node (optional; see README)
#   dtb/boot_unpacked/dtb       stock perseus DTB (already in the tree)
#
# Everything else is generated: ADT, payload, shim, boot image.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
OUT=$ROOT/out
mkdir -p "$OUT"

DASIZE=${DRAM_SIZE:-0xfd400000}
DABASE=${DRAM_BASE:-0x100000000}
PAD=1
[[ ${1:-} == --no-pad ]] && PAD=0

echo "== ADT =="
python3 tools/mkadt-perseus.py "$OUT/perseus.adt" \
    --highmem --dram-base "$DABASE" --dram-size "$DASIZE"

echo "== payload =="
python3 tools/mkpayload.py out/kernel.macho "$OUT/perseus.adt" "$OUT/payload.bin" \
    --cmdline "${CMDLINE:-debug=0xc serial=3 cpus=1 keepsyms=1 -v}"

echo "== shim =="
PATH=$HOME/bin:$PATH shim/build.sh "$OUT/payload.bin"

echo "== image =="
PATH=$HOME/bin:$PATH mkbootimg --header_version 1 \
    --kernel shim/out/Image --ramdisk "$OUT/payload.bin" \
    --base 0 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 --tags_offset 0x100 \
    --pagesize 4096 --os_version 10.0.0 --os_patch_level 2020-12 \
    --cmdline "console=null" -o "$OUT/perseus.img"

if [[ $PAD == 1 ]]; then
    # perseus' ABL only accepts images that fill the boot partition (64 MiB).
    python3 - "$OUT/perseus.img" "$OUT/perseus-64m.img" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
d = open(src, 'rb').read()
open(dst, 'wb').write(d + bytes(67108864 - len(d)))
print(f'{dst}: {67108864} bytes (padded from {len(d)})')
PY
    ls -l "$OUT/perseus-64m.img"
else
    ls -l "$OUT/perseus.img"
fi
