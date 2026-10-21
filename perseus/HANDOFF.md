# perseus (Xiaomi MIX 3, SDM845) — iOS 16 userland bring-up: handoff

Read this first. Everything below is either OBSERVED on hardware or on the
QEMU reproducer. Do not re-derive rejected hypotheses.

## TL;DR (2026-10-21)

The kernel boot chain works and XNU runs, but until now the boot died silently
in the IOKit phase: after `SWEET FB: AppleCLCD ...` the machine went idle with
no panic and no further console output. That was **not** the UFS driver and
**not** the console being stolen: the ADT's boot CPU node carried
`compatible = "qcom,kryo385"`, while the SweetCPU driver (patch 0018) matches
`IONameMatch = "qcom,kryo470"`. The CPU driver never matched, the CPUs were
never registered, and the boot thread sat waiting for them.
`tools/mkadt-perseus.py` now emits `qcom,kryo385` + `qcom,kryo470` (IODTMatch
walks every NUL-separated `compatible` entry), so the boot gets past IOKit and
reaches `IOFindBSDRoot` / the UFS root device. Next step is the UFS probe, not
the console.

## The QEMU reproducer (use this before touching the phone)

The stall reproduces with the stock CI kernel and the perseus ADT, so a phone
boot is not needed for this class of bug:

    cd ~/ios-sweet
    export PATH=$HOME/ios/qemu-8.2.2/build/aarch64-softmmu:$PATH
    python3 tools/mkadt-perseus.py /tmp/q.adt --qemu --no-base
    python3 tools/mkpayload.py ~/port/kernel.macho /tmp/q.adt /tmp/q.bin \
        --cmdline 'debug=0x14e serial=3 cpus=1 keepsyms=1 -v -enable_kprintf_spam rd=ufsroot0'
    python3 tools/qemu-sweet.py /tmp/q.bin /tmp/qout --secs 60 \
        --kernel ~/port/kernel.macho

`qemu-sweet.py` dumps `FB_BASE = 0x9c000000 / 1080x2400` by default; for the
perseus ADT patch those constants to `0x9d400000 / 1080x2340` (one `sed`) or the
late console text is decoded from the wrong address.

Measured A/B, same kernel, same cmdline, only the ADT differs:

| CPU `compatible` | result |
| --- | --- |
| `qcom,kryo385` | stops after `SWEET FB: AppleCLCD`, no panic, CPU idle (`PC=_ml_get_timebase`, `LR=_processor_idle`) |
| `qcom,kryo385` + `qcom,kryo470` | `SWEET CPU: 1/1 CPUs initialized, SGI1 verified`, `bsd_autoconf: calling kminit`, skywalk self-tests, `ifnet_attach`, then `IOFindBSDRoot` (and the expected panic in QEMU because it has no `/sweet-ufs` hardware) |

Note the screen console is a **circular** log (`fbcon_newline` wraps to row 0
and clears the two rows below the cursor), so the newest line is the one
followed by blank rows, not the bottom-most line of the screen. Reading the
bottom of the screen as "the end of the log" is how the previous handoff
concluded the boot stopped at `debug_log_init`; it did not.

## The one packaging recipe that works (OBSERVED)

Now scripted: `perseus/build.sh` (formerly a manual sequence).

    kernel section = gzip(shim Image) followed by the stock DTB blob
                     (1,237,374 B from boot-stock.img, offset 0xd947cd..0xec294b)
    ramdisk        = the mkpayload payload (kernel + ADT + boot-args)
    header         = v1, page_size 2048
    offsets        = kernel 0x10000000, ramdisk 0x10000000, tags 0x10000000
    size           = do NOT pad to the 64 MiB partition; 17 MB boots

    KERNEL=out/kernel.macho perseus/build.sh out/perseus.img

The default cmdline is
`debug=0x14e serial=3 cpus=1 keepsyms=1 -v -enable_kprintf_spam rd=ufsroot0`.

## ADT requirements (OBSERVED)

- compatible[0] MUST be `sweet`, otherwise
  `Unable to find driver for this platform: D22AP` @IOPlatformExpert.cpp:2319.
- **The boot CPU node must carry `qcom,kryo470`** (the SweetCPU
  `IONameMatch`), in addition to the real `qcom,kryo385`.
- Generator: `tools/mkadt-perseus.py --highmem --dram-base 0x100000000
  --dram-size 0xfd400000 --no-base` (small hand-built tree). Merging the whole
  D22AP tree (`--base`) makes XNU trip over Apple's absolute pointers
  (`Device tree pointer outside of device tree region`) and hang even earlier:
  rejected.
- `/sweet-ufs` = compatible `qcom,ufshc` + reg 0x1d84000/0x2500.
- `/sweet-fbcon` = 0x9d400000/0x2400000, 1080x2340, stride 4320 -> works.
- The early console (`xnu/patches/0010`, `sweet_early.c`) still hardcodes
  sweet's framebuffer 0x9c000000, which on perseus is plain RAM outside XNU's
  DRAM window: harmless but invisible. Cosmetic until someone needs it.

## Rejected hypotheses (do not retry)

- "ABL powers the panel off without a splash_region node": false for perseus.
- "Image must be padded to 64 MiB": false.
- "Kernel must be raw Image": false; it must be gzip. A raw shim is accepted
  by fastboot ("Booting OKAY") but never entered.
- "UFS PHY/clock port needed": the driver does no PHY init; it reuses the host
  state ABL left (same approach as the shim's UFS code).
- "The console is taken over by the IOKit progress console": false. The
  screen simply wraps; see above.
- Full D22AP ADT merge (`mkadt-perseus.py` without `--no-base`): rejected.

## Next actions (in order)

1. Boot `out/perseus.img` with the fixed ADT (`fastboot boot`) and confirm the
   `SWEET CPU:` lines plus `SWEET UFS:` on screen. If `SWEET UFS:` reports
   `host not ready`, capture the values and port the SM7150 -> SDM845
   clock/PHY differences (see perseus/UFS-PORTING.md).
2. Only if the UFS read path works: GPT, `rd=ufsroot0` root mount, dyld.
3. Kernel changes need CI: push to the `perseus` branch (see below).

## CI

The macOS `build` job works on the fork `wzy114514sb/ios-sweet` (about 7 min);
the `bootimg` job now runs `perseus/build.sh` and uploads `out/perseus.img`.
Push to the fork's `perseus` branch (the upstream `dipa-studio/ios-sweet` only
grants pull to this account).

## Artifacts

- Kernel:  perseus-port/ci/kernel2/kernel.development.sweet (sha256 4c3c04a3..)
- Root:    D:\MI845\IOS16-perseus\ios16-root.simg (12.1 GB)
           already flashed to userdata
- Working images (fastboot boot, nothing is flashed):
  perseus-k470.img  -> reaches the UFS probe (this handoff)
  perseus-spam.img  -> old ADT: stops after AppleCLCD
- Sources: ~/ios-sweet (branch perseus, fork wzy114514sb/ios-sweet)
           perseus/UFS-PORTING.md (kept for the UFS port proper)
- xnu reference source (tag xnu-8796.141.3) is in ~/xnu-8796 on the VM.

## Environment (VM 192.168.52.129, user wzy)
- ~/miniconda/bin/python 3.14, iasl/nasm/clang/llvm/mono installed
- ~/bin/mkbootimg (own tool, also committed as tools/mkbootimg for CI)
- QEMU 8.2.2 at ~/ios/qemu-8.2.2/build/aarch64-softmmu (the distro QEMU 4.2
  has no cortex-a76 model; qemu-sweet.py needs it)

## Facts about the device (from stock DTB and live DT)

RAM 0x80000000+0xC0000000 and 0x140000000+0xBD4A0000
XNU window used by the ADT: 0x100000000 + 0xfd400000 (no carve-outs)
splash 0x9D400000+0x2400000 | GIC 0x17A00000/0x17A60000 INTID 19
UFS 0x1D84000 | panel 1080x2340 Samsung EA8076 | touch FT5x46 @ i2c 0x38
boot.img stock: v1, page 4096, kernel 0x8000, ramdisk 0x1000000, tags 0x100
