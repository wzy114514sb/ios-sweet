#pragma once

/* Log the DSI/MDSS state ABL left behind (safe, read-only). */
void panel_probe(void);

#ifdef PERSEUS_PANEL_WAKE
/* Try to bring the panel back without re-initialising MDSS/DSI/PHY. */
int panel_wake(void);
#endif
