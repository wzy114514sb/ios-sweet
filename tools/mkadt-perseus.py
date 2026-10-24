#!/usr/bin/env python3
"""Build the Apple Device Tree for the Xiaomi MIX 3 (perseus, SDM845).

Derived from ios-sweet's tools/mkadt.py. Everything marked VERIFIED below was
read out of the stock perseus boot.img DTB (see out/dtb/), not copied from
another phone.

    python3 mkadt-perseus.py OUT.adt --dram-size 0xfc000000 --highmem [--dump]

Constants that the bootloader fills in at runtime (/memory reg is all zeros in
the static DTB) stay command-line arguments on purpose: pass the value you read
from the live device tree, do not guess it.
"""
import argparse
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import adt  # noqa: E402

# ---------------------------------------------------------------- VERIFIED
# /soc/interrupt-controller@17a00000 from boot-stock.img
GICD, GICD_SIZE = 0x17A00000, 0x10000
GICR, GICR_SIZE = 0x17A60000, 0x100000
# /soc/ufshc@1d84000 from boot-stock.img
UFS, UFS_SIZE = 0x01D84000, 0x2500
# /reserved-memory/cont_splash_region@9d400000 from boot-stock.img: the buffer
# ABL hands over already drawable. 0x2400000 = 3 x 1080x2340x4 rounded up.
FB, FB_SIZE = 0x9D400000, 0x2400000
# Panel geometry, MIX 3. The canvas stays the iPhone X size iOS expects; the
# framebuffer driver scales 1080x2340 into it.
PANEL_W, PANEL_H, PANEL_STRIDE = 1080, 2340, 1080 * 4
CANVAS_W, CANVAS_H = 1125, 2436
# /cpus/cpu@N reg from boot-stock.img: Aff1 = 0,0x100,...,0x700 (so core << 8).
CORES = 8
GOLD = 0x400                 # cores >= this are the Cortex-A75 cluster
CLOCK_GOLD, CLOCK_SILVER = 2803200000, 1804800000
# /arm-io children are relative to this; must be < every MMIO address and != 0
SOC_BASE = 0x01000000
TIMEBASE = 19200000          # ARM generic counter, expected 19.2 MHz on SM8xx
VTIMER_INTID = 19            # kernel convention inherited from ios-sweet

# ---------------------------------------------------------------- FALLBACKS
# Only used with --lowmem. Carve-outs in the stock DTB end at 0x98000000
# (pil_spss_region@97f00000 + 0x100000); everything above that up to 4 GiB is
# bootloader-owned and may move, so treat a low-memory build as an experiment.
LOW_PHYS_BASE, LOW_MEM_SIZE = 0xC0000000, 0x3C000000

# QEMU virt, for M3 (tools/qemu-sweet.py). Unchanged from ios-sweet.
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE, QEMU_VTIMER_INTID = 0x08000000, 0x080A0000, 62500000, 27

BOOT_MANIFEST_HASH = hashlib.sha384(b'perseus boot manifest').digest()
SWEET_ECID = 0x5357454554000001     # synthetic; iPhone X userland does not check it

# Nodes we rebuild from perseus hardware facts; everything else is kept from
# the real iPhone X (D22AP) tree so the kernel sees a complete device tree.
BASE_NODES = {'product', 'options', 'filesystems', 'osenvironments', 'aliases',
              'pram', 'vram', 'buttons', 'dock', 'dock-0pin', 'baseband', 'sacm',
              'defaults', 'backlight'}

D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '..', 'research', 'ipsw-x', 'd22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """Copy /product from the real iPhone X device tree (MobileGestalt reads it)."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for child in root.children:
        if dict((k, v) for k, _f, v in child.props).get('name') == b'product\0':
            props = [(k, v) for k, _f, v in child.props
                     if not _f and k not in ('name', 'AAPL,phandle')]
            # The /product node comes from an iPhone X (d22ap), whose screen is
            # 375x812 at 3x (1125x2436 pixels).  This port presents a 1170x2532
            # display (390x844 at 3x, the size whose asset renditions are
            # complete); CoreAnimation renders at the point size the device
            # reports, so it drew 1125x2436 into surfaces the 1170x2532 display
            # never showed.  Say it is an iPhone 12 (iPhone13,2), the device that
            # has this screen.
            props = [(k, b'iPhone13,2\0' if k == 'product-name' else v) for k, v in props]
            return props
    raise SystemExit(f'{path}: no /product node')


u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False, dram_size=None, dram_base=None,
          dev_identity=True, names='sweet'):
    """names='sweet' keeps the node names the SWEET-configured kernel looks up
    (/sweet-fbcon, /sweet-ufs, sweet-io, sweet,display); 'perseus' is for a
    kernel built with its own machine config."""
    if highmem:
        # Live DT (TWRP) on this phone: bank1 0x80000000+0xc0000000,
        # bank2 0x140000000+0xbd4a0000. 0x100000000..0x1fd4a0000 is contiguous
        # RAM (tail of bank1 + all of bank2) and free of carve-outs, so the
        # default window starts at 4 GiB like sweet; pass --dram-base
        # 0x140000000 to stay inside the second bank only (~2.96 GiB).
        phys_base = 0x100000000 if dram_base is None else dram_base
        if not dram_size:
            raise SystemExit('--highmem needs --dram-size (read it from the live DT)')
        mem_size = dram_size
    else:
        phys_base, mem_size = LOW_PHYS_BASE, LOW_MEM_SIZE

    gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('perseus-shim')),
        ('system-firmware-version', cstr('perseus-shim')),
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(3)),
        ('unique-chip-id', struct.pack('<Q', SWEET_ECID)),
        ('chip-id', u32(0x8015)),       # A11, the SoC the iPhone X userland expects
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    cpu_nodes = []
    for core in range(1 if qemu else CORES):
        gold = core >= GOLD // 0x100
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            # SweetCPU (xnu/patches/0018) matches IONameMatch 'qcom,kryo470';
            # IODTCompareNubName tries every compatible entry, so keep the
            # real SDM845 name first and the driver's name second.
            ('compatible', cstr('qcom,kryo385') + cstr('qcom,kryo470') + cstr('ARM,v8')),
            ('reg', u32(core << 8)),    # VERIFIED: Aff1 = 0x000..0x700 on perseus
            ('cpu-id', u32(core)),
            ('cluster-id', u32(1 if gold else 0)),
            ('cluster-type', u32(0)),   # homogeneous CLUSTER_TYPE_SMP, as on sweet
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('clock-frequency', u32(CLOCK_GOLD if gold else CLOCK_SILVER)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)), ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(QEMU_VTIMER_INTID if qemu else VTIMER_INTID)),
        ('reg', u64(gicd - SOC_BASE, GICD_SIZE, gicr - SOC_BASE, GICR_SIZE)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr(f'{names}-io')),
        ('compatible', cstr(f'{names}-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr(f'{names},display')),
            ('canvas-width', u32(CANVAS_W)),
            ('canvas-height', u32(CANVAS_H)),
        ])])

    fbcon = node(f'{names}-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(PANEL_W)),
        ('height', u32(PANEL_H)),
        ('stride', u32(PANEL_STRIDE)),
    ])

    ufs = node(f'{names}-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product() if dev_identity else None
    product = [node('product', [ph()] + product_props)] if product_props else []

    return node('device-tree', [
        ph(),
        ('compatible', cstr('sweet') + cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,sweet') + cstr('qcom,sm7150') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + ([] if qemu else [ufs]))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('output')
    parser.add_argument('--dump', action='store_true')
    parser.add_argument('--qemu', action='store_true', help='QEMU virt addresses (M3)')
    parser.add_argument('--highmem', action='store_true', help='use the >= 4 GiB bank')
    parser.add_argument('--dram-base', type=lambda v: int(v, 0), help='default 0x100000000')
    parser.add_argument('--dram-size', type=lambda v: int(v, 0), help='high bank size')
    parser.add_argument('--no-identity', action='store_true', help='omit /product')
    parser.add_argument('--no-base', action='store_true', help='do not merge onto the D22AP tree')
    parser.add_argument('--names', choices=('sweet', 'perseus'), default='sweet',
                        help='node names the kernel looks up (default: sweet, matching the SWEET kernel)')
    parser.add_argument('--seed', type=lambda v: int(v, 0))
    args = parser.parse_args()

    root = build(seed=args.seed, qemu=args.qemu, highmem=args.highmem,
                 dram_size=args.dram_size, dram_base=args.dram_base,
                 dev_identity=not args.no_identity, names=args.names)
    if not args.no_base and os.path.exists(D22AP_ADT):
        base_root, _ = adt.parse(open(D22AP_ADT, 'rb').read())
        kept = [c for c in base_root.children if c.name in BASE_NODES]
        merged = kept + [c for c in root.children if c.name not in BASE_NODES]
        base_root.children = merged
        base_root.props = root.props          # perseus identity, not D22AP's own
        root = base_root
        print(f'merged onto D22AP base: kept {len(kept)}, rebuilt {len(merged)-len(kept)}', file=sys.stderr)

    blob = adt.serialize(root)
    with open(args.output, 'wb') as handle:
        handle.write(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'{args.output}: {len(blob)} bytes', file=sys.stderr)


if __name__ == '__main__':
    main()
