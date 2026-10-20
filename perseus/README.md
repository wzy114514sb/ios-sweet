# perseus (Xiaomi MIX 3, SM845/SDM845) port branch

This branch carries the changes needed to run the sweet XNU build on a Xiaomi
MIX 3 (`perseus`, Qualcomm SDM845). It is a bring-up branch: the shim stays a
shim, the kernel is still the SWEET machine config, and everything is driven
from source rather than from hand-patched images.

## Build

```sh
perseus/build.sh            # -> out/perseus-64m.img
fastboot boot out/perseus-64m.img
```

`perseus/build.sh` runs the whole chain: ADT -> payload -> shim -> boot image.
The output is padded to 64 MiB because perseus' ABL refuses shorter images
(`Failed to load/authenticate boot image: Load Error`).

## What differs from upstream

### `tools/mkadt-perseus.py`

Generates the Apple Device Tree for perseus from values read out of the stock
perseus DTB and the live (ABL-patched) device tree:

| item | sweet | perseus |
| --- | --- | --- |
| GIC | 0x17a00000 / 0x17a60000 | same |
| UFS | 0x1d84000 | same |
| framebuffer | 0x9c000000, 1080x2400 | **0x9d400000, 1080x2340** |
| DRAM (high bank) | 0x100000000 + 0xfcc00000 | **0x100000000 + 0xfd400000** |
| CPU MPIDR | 0x000..0x700 | same |

`--names sweet` (default) keeps the node names the SWEET kernel looks up
(`/sweet-fbcon`, `/sweet-ufs`, `sweet-io`, `sweet,display`), so the stock
SWEET kernel works without a rebuild. `--names perseus` is for a kernel built
with its own machine config.

### `shim/`

* `console.h`, `mmu.h`: framebuffer constants are perseus' (0x9d400000,
  1080x2340). On perseus the ABL powers the panel down at handover, so this
  only matters once a panel driver exists.
* `ufs.c`: gained a SCSI `WRITE(10)` path (the upstream file is deliberately
  read-only) and `ulog()/ulog_hex()/ulog_flush()`, a debug log that is written
  to the last 4 blocks of the `userdata` partition. Read it back in TWRP:

  ```sh
  dd if=/dev/block/by-name/userdata of=/sdcard/perslog.bin bs=4096 \
     skip=$(( $(blockdev --getsize64 /dev/block/by-name/userdata)/4096 - 4 )) count=4
  ```

* `loader.c`: if `/chosen/linux,initrd-start` is missing, fall back to the
  header's `ramdisk_addr` (0x1000000) and derive the extent from the payload
  header. Logs the DTB pointer and the payload magic before failing.
* `shim.c`: stops (`wfe`) instead of resetting when the loader fails, so the
  message stays on screen; flushes the UFS log before halting and before
  jumping into XNU.

### Boot image format

Stock perseus images are header **v1**, and the boot partition is 64 MiB.
`perseus/build.sh` packs v1 and pads to 64 MiB.

## Known blockers

1. **The panel is off when the payload starts.** ABL blanks the DSI panel at
   handover, so neither the shim console nor the SWEET kernel's framebuffer
   (`AppleCLCD` writes into the ABL splash buffer) can be seen. A panel
   (Samsung EA8076) init sequence is required before any on-screen debugging.
2. `/chosen/linux,initrd-start` is not present in the FDT perseus' ABL passes,
   which is why the loader has the 0x1000000 fallback.

## Not included

`research/ipsw-x/d22ap.adt` (the iPhone X `/product` node used for the ADT
identity) is an Apple artifact and is not committed. Extract it from a
16.7.16 (20H392) iPhone X IPSW:

```sh
ipsw img4 im4p extract <ipsw>/Firmware/all_flash/DeviceTree.d22ap.im4p -o research/ipsw-x/d22ap.adt
```

Without it `mkadt-perseus.py` emits the ADT without `/product`.
