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

## Third session: SpringBoard runs; the missing piece is the renderer

The UI is no longer blocked by SpringBoard crashing.  What remains is that
CoreAnimation draws nothing at all, so every frame backboardd presents is black.

### What was fixed in this session

* **SpringBoard aborted in the corner-mask load.** `SBDashBoardSetupViewController`
  was presented and then SpringBoard died 82 times with "Failed to load bounding
  path bitmap data ... BoundingPathBitmap-1125x2436-375x812-3.00x".  In this
  build's `UIKitCore.framework/BoundingPathData.bundle/Assets.car` that name
  carries only the bare asset, while `1080x2340-375x812`, `1170x2532-390x844`
  and `1284x2778-428x926` also have the `-A` rendition and the external `.data`
  blob.  Patch 0087 presents the panel as 1170x2532 (390x844 points at 3x), so
  UIKit asks for a complete name.  SpringBoard stopped crashing.
* **The on-screen Apple logo and progress bar were the kernel's**, not iOS's:
  the macOS-flavoured build draws `vc_progress` over the whole panel and paints
  a black background, so every iOS frame was hidden behind it.  Boot with
  `-noprogress` (pexpert/arm/pe_init.c accepts it) and the panel shows only what
  userland presents.
* **backboardd spun forever on `AppleM2ScalerCSCDriver`** ("IOServiceMatchingfailed
  for: AppleM2ScalerCSCDriver", hundreds of lines, never finishing display
  setup).  Patch 0088 registers a stub service of that name with a user client
  that answers every selector with success; the loop is gone and backboardd logs
  `SWEET SCALER: backboardd sel 1`.
* Patch 0090 registers `IOAcceleratorES` / `IOAcceleratorESLegacy` stubs - they
  register, but CoreAnimation never opens them (no `SWEET ACCEL: <proc> sel N`),
  so they do not help.

### The measured blocker

Patch 0089 samples the surface that `present()` copies and logs an XOR and a
non-black pixel count.  Every sample reads:

    SWEET FB: present surface 5 1080x2340 xor 0x00000000 nonblack 0

So the frames are empty: backboardd swaps 1000+ 1170x2532 surfaces, SpringBoard
runs (sampler shows it idle in CFRunLoop, then doing RunningBoard work), Setup
is exec'd, the display is powered on - and every pixel is black.  The display,
the surface API, the present path and the window server are all fine; the
renderer is what is missing.

### The known-good approach (HackiOS)

`github.com/oosexgang/hackios-xnu-userland` runs the unmodified iOS 16.5
userland on a phone and in QEMU.  Its notes say CoreAnimation's render server
draws with Metal, else with OpenGL ES, else with its **own software
rasterizer**.  Two ways they make that work:

* `userland/src/inject.c` (injected into backboardd) patches CoreAnimation's
  C++ vtables so the render server takes its software rasterizer.  The addresses
  are those of **iOS 16.5 (20F66) iPhone10,3** and every one is verified before
  use ("found %#lx, expected %#lx; left alone"), so the prebuilt dylibs do not
  apply to our 16.7.16 (20H392) userland.
* the `GL=1` route: Mesa 25.0.7 built with the `softpipe` gallium driver (pure C
  rasterizer, no LLVM, no GPU) as `libOSMesa.8.dylib` (15 MB), plus
  `userland/src/eagl.c` -> `libredwood-gl.dylib`, which replaces the six
  `EAGLContext` methods CoreAnimation asks for and hands the GL work to OSMesa.
  With a working EAGL, CA picks its OpenGL ES renderer by itself - no CA
  patching needed.  `eagl.c` needs the table of iOS OpenGL ES entry points
  (`userland/src/eagl_slots.txt`) which is likewise 16.5-specific.

Our root image has none of this; Apple's `OpenGLES.framework` cannot render
without a GPU driver, so CA has no renderer and produces empty frames.

### Next actions, in order of expected value

1. **Mesa route.** Build Mesa 25.0.7 with `softpipe` for iOS arm64 in CI (the
   macOS runner has Xcode's iPhoneOS SDK; meson/ninja/bison/flex from brew),
   produce `libOSMesa.8.dylib`, compile a version of `eagl.c` against it, and
   drop both into the image at `/redwood/lib/`, injecting into backboardd and
   SpringBoard through launchd `EnvironmentVariables`
   (`DYLD_INSERT_LIBRARIES`) with `tools/patch-launchd-plist.py`.  The GL entry
   point table must be regenerated for 20H392 (see `userland/mkeagl.py`,
   `gl.xml`, `exported-gl.txt`).
2. **CA vtable route.** Find `CA::WindowServer::EAGLServer::renderer()` and the
   display-update path in *our* QuartzCore (dyld cache, 20H392) and patch the
   same way HackiOS does.  More reverse engineering than (1), but no Mesa build.
3. **USB live log** (patches 0072-0077): DWC3 + QUSB2 PHY from the stock DTB,
   CDC-ECM, console log over UDP to 192.168.7.1:5140.  Would end the TWRP round
   trips that every iteration currently needs.

### Facts worth remembering

* The "stalls" in early boot (seputil, `mount`, mobile_obliterator) are the
  polled UFS driver being extremely slow, not deadlocks: minutes later the same
  boot continues.  Give a boot 20+ minutes before calling it hung.
* Boot args that matter now:
  `-noprogress sweetfb_noconsole=1 ufsrw=1 rootrw=1 unentitled_ios_sim_launch=1
  sweetsample=SpringBoard,backboardd,com.apple.datami rd=ufsroot0 cpus=8`.
* `sweetsample` needs 16-character names (`com.apple.datami`).
* `SWEET FB: display off/on` is backboardd's power request (patch 0079), which
  fades the panel to black and holds it until a wake event.

## The software OpenGL ES stack (fourth session)

With the renderer missing, CoreAnimation produced empty frames (patch 0089
sampling: every presented surface `nonblack 0`).  The fix is the route HackiOS
uses: give CA a working OpenGL ES, and it draws with its GLES renderer - on the
CPU, through Mesa's softpipe driver, with no GPU driver at all.

### What is built, and how

* `tools/mesa/osmesa-es-profile.patch` (from HackiOS, MIT) gives OSMesa an
  OpenGL ES profile.
* `.github/workflows/mesa.yml` builds, in one macOS job:
  * Mesa 25.0.7 with `-Dgallium-drivers=softpipe -Dosmesa=true` against Xcode's
    iPhoneOS SDK -> `libOSMesa.8.dylib` (13 MB, arm64), install name
    `/redwood/lib/libOSMesa.8.dylib`;
  * HackiOS's `eagl.c` + `gl_osmesa.c` (vendored in `tools/redwood/`) linked
    against it -> `libredwood-gl.dylib` (346 KB).  The shim has its own
    constructor: on load it replaces the six `-[EAGLContext ...]` methods
    CoreAnimation asks for and hands every `gl*` call to OSMesa, so no
    CoreAnimation patching is needed (which the rest of HackiOS needs, and which
    is tied to iOS 16.5 addresses).
  * the mkeagl.py generated header comes from `tools/redwood/eagl_slots.txt`
    (the table offsets of iOS 16.5's OpenGL ES; OpenGL ES has been frozen since
    iOS 12, so it is expected to hold for 16.7 too - not yet verified).

GitHub could not hand out a macOS runner for hours ("The job was not acquired by
Runner of type hosted"), so the same two libraries can also be built locally on
the Linux VM: `theos/sdks` has `iPhoneOS16.5.sdk`, and clang-18 + lld-18 + that
SDK link Mach-O correctly (`-fuse-ld=lld`, framework TBs resolved through
`-isysroot`; Xcode 15.4 has no `llvm-ar`, so meson must use Apple's
`ar`/`ranlib`/`strip`).

### Installing it into a root image

    tools/redwood-image.sh ios16-root2.img libOSMesa.8.dylib libredwood-gl.dylib

puts both at `/redwood/lib/` and adds
`DYLD_INSERT_LIBRARIES=/redwood/lib/libredwood-gl.dylib` to
`com.apple.backboardd` in `/System/Library/xpc/launchd.plist`
(`tools/patch-launchd-inject.py`).  The image must be fsck'ed first (Linux will
not mount an unclean HFS+ volume read-write) and booted with
`launchd_unsecure_cache=1` so launchd reads that plist rather than its cache.

Boot args for this image:

    debug=0x14e serial=3 cpus=8 keepsyms=1 -v -enable_kprintf_spam -noprogress
    launchd_unsecure_cache=1 ufsrw=1 rootrw=1 unentitled_ios_sim_launch=1
    sweetfb_noconsole=1 sweetsample=SpringBoard,backboardd,com.apple.datami
    rd=ufsroot0

### What to look for in the log

    redwood-gl: N of 6 methods of EAGLContext are ours; OpenGL ES by ...
        the shim loaded and took over EAGLContext
    redwood-gl: <counts>
        CoreAnimation is actually drawing through it
    SWEET FB: present surface ... nonblack <non-zero>
        pixels reached the surface the panel copy takes

If the screen stays black with no `redwood-gl:` lines at all, the injection did
not happen: check that launchd took the plist (the boot arg) and that the
library's install name matches its path.
