/*
 * perseus panel bring-up (Samsung EA8076 command-mode DSI, SDM845).
 *
 * The ABL drives this panel for the splash, then powers it down before
 * handing over: after `fastboot boot` the screen goes black immediately and
 * never comes back, so neither the shim console nor the SWEET kernel's
 * framebuffer (which only writes into the ABL splash buffer) can be seen.
 *
 * This file starts with what can be done safely and blind: read the DSI
 * controller state ABL left behind and put it in the UFS log. Whether a cheap
 * "wake" (sleep-out + display-on over the still-configured link) is enough, or
 * the whole MDSS/DSI/PHY chain has to be re-initialised, depends on that
 * answer. The wake path is compiled in but off by default.
 */
#include <stdint.h>
#include "console.h"
#include "panel.h"
#include "ufs.h"

/* sdm845: mdss@ae00000, dsi@ae94000, dsi_phy@ae94400 */
#define MDSS_BASE	0x0ae00000UL
#define DSI_BASE	0x0ae94000UL
#define DSI_PHY_BASE	0x0ae94400UL

#define MDSS_HW_VERSION		(MDSS_BASE + 0x0000)
#define MDSS_MDP_CTRL		(MDSS_BASE + 0x0004)
#define MDSS_CLK_CTRL		(MDSS_BASE + 0x0010)

/* DSI register offsets, from mainline's registers/display/dsi.xml */
#define DSI_CTRL		0x000
#define DSI_STATUS0		0x004
#define DSI_FIFO_STATUS		0x008
#define DSI_CMD_DMA_CTRL	0x038
#define DSI_CMD_CFG0		0x03c
#define DSI_CMD_CFG1		0x040
#define DSI_DMA_BASE		0x044
#define DSI_DMA_LEN		0x048
#define DSI_TRIG_CTRL		0x080
#define DSI_TRIG_DMA		0x08c
#define DSI_LANE_STATUS		0x0a4
#define DSI_LANE_CTRL		0x0a8
#define DSI_INTR_CTRL		0x10c
#define DSI_CLK_CTRL		0x118
#define DSI_CLK_STATUS		0x11c
#define DSI_PHY_RESET		0x128

#define DSI_CTRL_ENABLE		(1u << 0)
#define DSI_CTRL_VIDEO_MODE	(1u << 1)
#define DSI_CTRL_CMD_MODE	(1u << 2)

static inline uint32_t rd32(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline void wr32(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static void delay_loops(uint32_t n)
{
	while (n--)
		__asm__ volatile("yield");
}

/* DCS command packet, DMA'd through the command engine.
 *
 * The controller formats the packet (DI + payload + ECC) itself when
 * INSERT_DCS_COMMAND is set, so the buffer only carries type/length/data. */
static uint8_t cmd_buf[64] __attribute__((aligned(64)));

static int dsi_send(uint8_t dtype, const uint8_t *data, int len)
{
	if (len > 32)
		return -1;
	cmd_buf[0] = dtype;
	cmd_buf[1] = (uint8_t)len;
	for (int i = 0; i < len; i++)
		cmd_buf[2 + i] = data[i];

	wr32(DSI_BASE + DSI_CMD_DMA_CTRL, 0);
	wr32(DSI_BASE + DSI_CMD_CFG0, 0);
	wr32(DSI_BASE + DSI_CMD_CFG1, 1u << 16);	/* INSERT_DCS_COMMAND */
	wr32(DSI_BASE + DSI_DMA_BASE, (uint32_t)(uintptr_t)cmd_buf);
	wr32(DSI_BASE + DSI_DMA_LEN, (uint32_t)((len + 2 + 3) & ~3u));
	wr32(DSI_BASE + DSI_INTR_CTRL, (1u << 0) | (1u << 1));	/* clear CMD_DMA_DONE */
	wr32(DSI_BASE + DSI_TRIG_CTRL, 4u);		/* DMA_TRIGGER = SW */
	wr32(DSI_BASE + DSI_TRIG_DMA, 1);
	__asm__ volatile("dsb sy" ::: "memory");

	for (uint32_t spin = 0; spin < 2000000u; spin++) {
		if (rd32(DSI_BASE + DSI_INTR_CTRL) & 1u) {
			wr32(DSI_BASE + DSI_INTR_CTRL, 1u);
			return 0;
		}
	}
	return -2;		/* timeout */
}

void panel_probe(void)
{
	ulog_hex("MDSS_HW", rd32(MDSS_HW_VERSION));
	ulog_hex("MDSS_CTRL", rd32(MDSS_MDP_CTRL));
	ulog_hex("MDSS_CLK", rd32(MDSS_CLK_CTRL));
	ulog_hex("DSI_HW", rd32(DSI_BASE + 0x000));
	ulog_hex("DSI_CTRL", rd32(DSI_BASE + DSI_CTRL));
	ulog_hex("DSI_STATUS0", rd32(DSI_BASE + DSI_STATUS0));
	ulog_hex("DSI_CLK_CTRL", rd32(DSI_BASE + DSI_CLK_CTRL));
	ulog_hex("DSI_CLK_STAT", rd32(DSI_BASE + DSI_CLK_STATUS));
	ulog_hex("DSI_LANE_STAT", rd32(DSI_BASE + DSI_LANE_STATUS));
	ulog_hex("DSI_PHY_RST", rd32(DSI_BASE + DSI_PHY_RESET));
	ulog_hex("DSI_PHY_REV", rd32(DSI_PHY_BASE + 0x0000));
	uint32_t ctrl = rd32(DSI_BASE + DSI_CTRL);
	if (ctrl & DSI_CTRL_ENABLE)
		ulog("PANEL", "DSI still enabled (ABL left the link up)");
	else
		ulog("PANEL", "DSI disabled (full init required)");
}

#ifdef PERSEUS_PANEL_WAKE
/* Cheap attempt: the link is (maybe) still configured, so only take the panel
 * out of sleep and turn the display on. EA8076 is a command-mode panel. */
int panel_wake(void)
{
	uint32_t ctrl = rd32(DSI_BASE + DSI_CTRL);
	if (!(ctrl & DSI_CTRL_ENABLE)) {
		ulog("PANEL", "wake skipped, DSI off");
		return -1;
	}

	static const uint8_t sleep_out[] = { 0x11 };
	static const uint8_t display_on[] = { 0x29 };

	if (dsi_send(0x05, sleep_out, 1)) {		/* DCS short write, no param */
		ulog("PANEL", "sleep-out failed");
		return -2;
	}
	ulog("PANEL", "sleep out sent");
	delay_loops(12000000);			/* EA8076 needs ~120 ms after 0x11 */

	if (dsi_send(0x05, display_on, 1)) {
		ulog("PANEL", "display-on failed");
		return -3;
	}
	ulog("PANEL", "display on sent");
	return 0;
}
#endif
