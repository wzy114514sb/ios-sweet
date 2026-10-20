#pragma once
#include <stdint.h>
void test_ufs(void);
/* Debug log persisted to the last blocks of the userdata partition. */
void ulog(const char *label, const char *msg);
void ulog_hex(const char *label, uint64_t v);
int ulog_flush(void);
