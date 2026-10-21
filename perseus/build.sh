#!/bin/bash
# Build the perseus boot image.
#
# Only one packaging shape is accepted *and entered* by the perseus ABL
# (all of this was measured on the phone, see perseus/HANDOFF.md):
#
#   kernel section = gzip(shim Image) followed by the stock perseus DTB blob
#   ramdisk        = the mkpayload payload (kernel + ADT + boot-args)
#   boot header v1, 2048-byte pages
#   kernel / ramdisk / tags offsets 0x10000000
#   NOT padded to the 64 MiB boot partition (a ~17 MB image boots)
#
# A raw, uncompressed shim is accepted by fastboot but never entered; padding
# the image to the partition size is not needed either.
#
#   perseus/build.sh [OUT]
#   KERNEL=out/kernel.macho CMDLINE='...' perseus/build.sh
#
# Inputs:
#   $KERNEL                    XNU Mach-O (default out/kernel.macho)
#   $CMDLINE                   kernel command line (default: the debug line
#                              used for every bring-up boot so far)
#   $DRAM_BASE/$DRAM_SIZE      ADT /chosen dram window (default: high bank)
#   $ADT/$PAYLOAD              intermediate files (default out/)
#   perseus/stock-dtb-blob.bin  stock DTB appended to the kernel section
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
OUTDIR=$ROOT/out
mkdir -p "$OUTDIR"

KERNEL=${KERNEL:-$OUTDIR/kernel.macho}
ADT=${ADT:-$OUTDIR/perseus.adt}
PAYLOAD=${PAYLOAD:-$OUTDIR/payload.bin}
IMG=${1:-$OUTDIR/perseus.img}
CMDLINE=${CMDLINE:-'debug=0x14e serial=3 cpus=1 keepsyms=1 -v -enable_kprintf_spam ufsrw=1 rootrw=1 unentitled_ios_sim_launch=1 rd=ufsroot0'}
DRAM_BASE=${DRAM_BASE:-0x100000000}
DRAM_SIZE=${DRAM_SIZE:-0xfd400000}
DTB=${DTB:-$ROOT/perseus/stock-dtb-blob.bin}
PAGE=${PAGE:-2048}
KERNEL_OFFSET=${KERNEL_OFFSET:-0x10000000}

[[ -f $KERNEL ]] || { echo "no kernel at $KERNEL (set KERNEL=)" >&2; exit 1; }
[[ -f $DTB ]] || { echo "no stock DTB at $DTB" >&2; exit 1; }

MKB=$(command -v mkbootimg || true)
if [[ -z $MKB ]]; then
    MKB=$ROOT/tools/mkbootimg
    chmod +x "$MKB"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "== ADT =="
python3 tools/mkadt-perseus.py "$ADT" --highmem --dram-base "$DRAM_BASE" \
    --dram-size "$DRAM_SIZE" --no-base

echo "== payload =="
python3 tools/mkpayload.py "$KERNEL" "$ADT" "$PAYLOAD" --cmdline "$CMDLINE"

echo "== shim =="
# The shim's own v2 boot.img is not used on perseus (the recipe below packs the
# image), so do not require mkbootimg or the dtb/ dump inside shim/build.sh.
SHIM_SKIP_PACK=1 CC=${CC:-aarch64-linux-gnu-gcc} \
    OBJCOPY=${OBJCOPY:-aarch64-linux-gnu-objcopy} \
    bash shim/build.sh "$PAYLOAD"

echo "== image =="
gzip -9 -c shim/out/Image > "$TMP/shim.gz"
cat "$TMP/shim.gz" "$DTB" > "$TMP/kernel-section"
"$MKB" --header_version 1 --kernel "$TMP/kernel-section" --ramdisk "$PAYLOAD" \
    --base 0 --kernel_offset "$KERNEL_OFFSET" --ramdisk_offset "$KERNEL_OFFSET" \
    --tags_offset "$KERNEL_OFFSET" --pagesize "$PAGE" --os_version 17.0.0 \
    --os_patch_level 2035-06 --cmdline "console=null" -o "$IMG"
ls -l "$IMG"
