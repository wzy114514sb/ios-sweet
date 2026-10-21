# perseus UFS root — progress log

Status: kernel boots on hardware to the root-device wait. The progress bar
advances (timers work), `VM bootstrap done` is printed, and then nothing:
no UFS, apfs or hfs line ever appears. So the UFS driver never probes.

## OBSERVED (do not re-derive)
- Boot chain works only with: kernel = gzip(shim) + DTB appended, page_size
  2048, kernel/ramdisk/tags offsets 0x10000000, NOT padded to the partition.
  Any other shape is rejected or silently not entered.
- ADT must have compatible[0] = "sweet", otherwise:
  panic "Unable to find driver for this platform: D22AP".
- Panel is fine: SWEET FB prints 1080x2340 stride 4320 at 0x9d400000.
- ios16-root.simg (12.1 GB, sha256 7bd7ae17...) is flashed to userdata.
- Boot args used: debug=0x14e serial=3 cpus=1 keepsyms=1 -v
  -enable_kprintf_spam rd=ufsroot0

## Driver code
Added by xnu/patches/0028-sweet-ufs-root-device.patch as bsd/dev/sweet_ufs.c
(580 lines), plus 0030 (writes), 0034 (DMA above 4 GiB), 0069 (speed).
sweet_ufs.c carries SM7150 (sweet) PHY/clock/reset setup.

## What has to change for SDM845 (perseus)
1. UFS host is the same 0x01d84000; UFS PHY is 0x01d87000.
2. PHY init sequence differs (SM7150 vs SDM845 QMP PHY). Source of truth:
   vendor kernel `drivers/scsi/ufs/ufs-qcom.c` + `ufs-qcom-ops` tables for
   sdm845, or the DT properties qcom,phy-* in sdm845.dtsi.
3. GCC UFS clocks: the clock/reset register offsets are per-SoC; the vendor
   `gcc-sdm845.c` lists them (UFS_AXI_CLK, UFS_UNIPRO, UFS_ICE, PHY_AUX).
4. Regulators: vddp-ref-clk / vcc / vccq rails come from RPMh; check what
   sweet_ufs.c assumes vs what perseus provides.

## Test loop (each iteration)
1. Edit bsd/dev/sweet_ufs.c in the xnu patch series (0028).
2. CI: push to the perseus branch, xnu.yml builds the kernel (macOS runner).
3. Pack: gzip(shim) + stock DTB, ramdisk = payload, page 2048, offsets 0x10000000.
4. fastboot boot, watch for the first UFS line.
Do not change anything else between iterations: the packaging recipe above is
the one verified to reach XNU.

## Next action
Read bsd/dev/sweet_ufs.c (after applying 0028..0069) and list every hardcoded
SM7150 register/clock/PHY constant; that list is the porting surface.
