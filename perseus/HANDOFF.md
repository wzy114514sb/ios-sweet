# perseus (Xiaomi MIX 3, SDM845) — iOS 16 userland bring-up: handoff

Read this first. Everything below is OBSERVED on hardware (2026-10-21). Do not
re-derive the rejected hypotheses.

## TL;DR

The port now boots iOS 16 userland on the phone: launchd runs as PID 1, the
whole core daemon set starts once and does not crash, userland draws through
our display path (verified with real IOSurface swaps), and the first-boot data
migration completes with the on-screen progress UI.

The current blocker is the last step to a UI: **SpringBoard's main thread is
blocked in a synchronous XPC call** (`libsystem_kernel: mach_msg2_trap ->
libdispatch -> libxpc -> Foundation -> CoreServices`) and nothing paints after
the migration bar finishes. `usermanagerd` does create the default session
(uid 501 / mobile), so this is not the session itself; see "Open questions".

## What works (observed on the phone)

| layer | evidence |
| --- | --- |
| boot chain | `perseus/build.sh` recipe (gzip(shim)+stock DTB, v1, 2048, offsets 0x10000000) |
| CPU driver | ADT CPU node must list `qcom,kryo470` (SweetCPU's IONameMatch) |
| UFS | host takeover works; boot args need `ufsrw=1` or the device is read-only |
| HFS+ root | mounts read-write with `rootrw=1`; `hfs: mounted iOSRoot on device b(1, 0)` |
| launchd | PID 1, early boot tasks, `launchd_cache_loader` passes with patch 0083 |
| daemons | runningboardd/backboardd/SpringBoard/securityd/keybagd/watchdogd/notifyd/configd/logd/... all exec=1 crash=0 |
| display | `IOK backboardd: open AppleCLCD type 0 -> 0x0`, `SWEET SURF: backboardd create id 3 1125x2436`, `SWEET FB: swap 2 layer 0 surface 2 1125x2436 fmt 0x42475241` |
| input | `SWEET KEYS: key=0 down=1` -> `SWEET HID: key 0x30 down (1 clients)` (SpringBoard listens) |
| migration | progress UI drawn by backboardd, bar reaches 100%, datamigrator + migrationpluginwrapper ran |

Boot args of the working image:

    debug=0x14e serial=3 cpus=1 keepsyms=1 -v -enable_kprintf_spam \
    ufsrw=1 rootrw=1 unentitled_ios_sim_launch=1 rd=ufsroot0

## Root image recipe (this is the one that is stable)

`tools/mkiosroot.sh` as usual, but the volume is created with preallocated
metadata so the btrees never grow and never fragment:

    mkfs.hfsplus -s -J -v iOSRoot -c c=49152,e=8192,b=512 "$out"

(catalog 192 MiB, extents 32 MiB, bitmap 2 MiB, each a single extent.) The
default 4 MiB catalog grew to 154 MiB in 8 extents on the first image, which
caused two separate failures:

1. `hfs_swap_BTNode: offsets ... out of order` + `Runtime corruption detected`
   whenever a btree node (8192 B = 2 allocation blocks) straddled an extent
   boundary: the kernel reads the node's second block from the physically next
   block instead of the block the extent maps, e.g. file block 17923 mapped to
   216063 (`extent6.start + 3`) instead of 216075 (`extent7.start`).
2. `hfs: extendbtree: fcb ... has eof ... not a multiple of 0x2000` panic when
   userland first created files and the catalog had to grow (it had 24 free
   nodes). Preallocating removes both.

The volume also gets `/private/var/keybags/systembag.kb` (plain plist) from the
generator; there is no `persona.kb` and no `containers/` yet.

## Reading the console after a boot (no phone screen needed)

Kernel patch 0047 mirrors the whole console into a 4 MiB ring and flushes it to
the last 4100 KiB of userdata every second. Pull it from TWRP:

    fastboot boot TWRP-3.7.1-A14-non-Dynamic.img
    # adb shows state "recovery"
    adb shell 'blockdev --getsize64 /dev/block/by-name/userdata'   # 106886594560
    # skip = size/4096 - 1025
    cmd /c "adb exec-out \"dd if=/dev/block/by-name/userdata bs=4096 \
        skip=26094335 count=1025 2>/dev/null\" > perslog.bin"
    python tools/sweet-log.py perslog.bin perslog.txt

`perslog.txt` is the full kernel console of the session (659 KB for a ~1 h
boot). Note that the fbcon is a *circular* screen log: the newest line is the
one followed by blank rows, not the bottom-most line.

## Symbolicating the sampler output

Kernel patch 0054 samples `backboardd`/`SpringBoard` threads and prints raw
addresses (`SMP <proc>[pid] tN pc ... bt ...`). They can be resolved offline
against the dyld shared cache in the image:

* parse `/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64`
  (image list = 32-byte entries: u64 address, u64 mtime, u64 inode, u32 path
  offset, u32 pad; 2031 images in this build),
* unslide with the `slide` value the crash printer reports (0x27130000 here),
* map each address to `path + offset`.

`work/symbolize.py` + `work/dsc_images.json` in the workspace do exactly that.
What it shows for the current stall:

* SpringBoard t0: `mach_msg2_trap -> libdispatch -> libxpc -> Foundation ->
  CoreServices` (a synchronous XPC send with reply), t1: FrontBoard loop,
  the rest idle.
* backboardd: many threads in `mach_msg2_trap`, several in QuartzCore
  (compositor running), i.e. healthy.

## Open questions / next actions

1. **Which XPC is SpringBoard waiting for?** The stack is a synchronous
   Foundation/xpc call. Candidates: the shell check-in it performs against
   backboardd, or a FrontBoard/runningboardd service. Next step is to make the
   kernel print the blocked message's `msgh_id` (or the port name) for
   SpringBoard instead of raw addresses, then compare with the same boot on a
   tolerant boot-arg set.
2. **AKS selectors the migration still needs**: `com.apple.datamigrator`
   calls selector 93 (arg 0xffffffffffffffff) and 34, both answered
   `0xe00002c7` (kIOReturnUnsupported). Implementing them may unblock the
   migration's last stages.
3. **Personas**: `usermanagerd` created the default session, but
   `/private/var/keybags/persona.kb` and `containers/` do not exist and
   persona creation needs AKS key wrapping (no SEP). Either pre-bake persona
   data into the image or patch the consumers.
4. **Noise to ignore for now**: `fairplayd.H2`, `bluetoothd`, `driverkitd`,
   `SiriTTSSynthesizerAU`, `MacinTalkAUSP`, `KonaSynthesizer` crash-loop
   (SIGABRT / jetsam) because audio HAL, DriverKit services and the GPU
   (`IOAcceleratorES`) do not exist. None of them is on the boot path.

## Kernel changes added on top of the original series

* 0082 (temporary): dump the UFS root partition's first block, the volume
  header fields and the catalog btree header to the console.
* 0083: publish a built-in `AppleMobileFileIntegrity` IOService + user client.
  `launchd_cache_loader` does `IOServiceGetMatchingService(
  IOServiceMatching("AppleMobileFileIntegrity"))`, opens a user client and
  calls selector 6 with `{1}` scalar + the cdhash; success means "trusted".
  Without the service it aborts and, being a PID 1 child, panics the machine.

## Environment

* VM 192.168.52.129 (user wzy, ssh key installed from the Windows host).
* QEMU 8.2.2 at ~/ios/qemu-8.2.2/build/aarch64-softmmu (the distro QEMU 4.2
  has no cortex-a76 model).
* xnu-8796.141.3 source in ~/xnu-8796; Apple hfs source in ~/hfs-627.
* CI: push to the fork `wzy114514sb/ios-sweet` branch `perseus` runs `xnu.yml`
  (macOS build ~7 min, plus the boot image job). Upstream
  `dipa-studio/ios-sweet` grants pull only.

## Facts about the device (from stock DTB and live DT)

RAM 0x80000000+0xC0000000 and 0x140000000+0xBD4A0000
XNU window used by the ADT: 0x100000000 + 0xfd400000 (no carve-outs)
userdata partition: /dev/block/sda23, 106,886,594,560 B (26,095,360 x 4096)
splash 0x9D400000+0x2400000 | GIC 0x17A00000/0x17A60000 INTID 19
UFS 0x1D84000 | panel 1080x2340 Samsung EA8076 | touch FT5x46 @ i2c 0x38

## Latest findings (second session, after the first userland boots)

### SMP works on perseus

Booted with `cpus=8`: the ADT already declares eight CPU nodes, and the kernel's
PSCI path brings the secondaries up on real hardware:

    SWEET CPU: requested=8 selected=8 (limit=8)
    SWEET CPU: cpu=1 CPU_ON target=0x100 entry_pa=0x10148e000 context=1
    SWEET CPU: cpu=1 PSCI CPU_ON rc=0
    SWEET CPU: cpu=1 mpidr=0x100 came alive; SGI1 OK     (cpu 2..7 likewise)

So the single-core limit was only the `cpus=1` boot arg, not a missing driver.

### Where the UI actually stops (symbolicated, 40 samples each)

SpringBoard t0 (all 40 samples identical):

    libsystem_kernel(mach_msg2) -> libdispatch -> libxpc
    -> BoardServices.framework + 0x3128/+0x5e78/+0x5554

i.e. the BoardServices (BKS) system-shell check-in. backboardd never answers
it because its own worker sits in:

    libsystem_kernel(mach_msg2) -> libdispatch -> libxpc
    -> DataMigration.framework + 0x5334          (backboardd t1)

That is `DMPerformMigrationIfNeeded`: backboardd waits for the data-migration
service, and until it returns it keeps SpringBoard's shell check-in pending.
The boot progress UI reaches 100% and then holds forever.

### The migration service is the suspect

`com.apple.datamigrator` is exec'd, calls AKS selectors 0, 101, 93, 34, 17 and
then goes silent; 93 (arg 0xffffffffffffffff) and 34 both return
kIOReturnUnsupported (0xe00002c7) from our AKS stub. `migrationpluginwrapper`
runs 2-3 times.

Two image problems found while looking for it:

* there is no `datamigrator` / `migrationpluginwrapper` binary under
  `/usr/libexec` (282 entries, none matching) nor anywhere else in the volume,
  and no `com.apple.datamigrator.plist`, yet a process of that name ran;
* the App cryptex copy is only 11 MiB (three metadata files) and the OS cryptex
  has no `usr/libexec` at all, so the image is probably an incomplete copy of
  the source DMGs.

Next actions, in order:

1. Compare the built image against the three source DMGs (file count and size
   per top-level directory) and re-copy whatever `mkiosroot.sh` dropped. The
   missing App cryptex content is the most likely reason the migration never
   finishes.
2. Make the AKS stub answer the unimplemented selectors (34, 93, ...) with
   success plus zeroed output instead of kIOReturnUnsupported, so the migration
   can proceed even without the SEP-backed calls.
3. If the migration still does not finish, pre-bake the migration state the
   service expects under `/private/var/datamigrator` (it exists but is empty on
   our image).

### Sampler gotcha

XNU process names are 16 characters (`MAXCOMLEN`), so `sweetsample=` must use
the truncated names: `com.apple.datami`, not `com.apple.datamigrator`.
