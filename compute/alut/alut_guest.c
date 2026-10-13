// SPDX-License-Identifier: MIT
/* mmap guest NTB BAR2 at peer_off and poll the ALUT mailbox header. */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define MAGIC 0x414c5554u

int main(int argc, char **argv)
{
	const char *bar = argc > 1 ? argv[1] : "/sys/bus/pci/devices/0000:01:00.0/resource2";
	unsigned long off = argc > 2 ? strtoul(argv[2], NULL, 0) : 0;
	int fd;
	uint32_t *p;
	int i;

	fd = open(bar, O_RDWR | O_SYNC);
	if (fd < 0) {
		perror(bar);
		return 1;
	}
	p = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)off);
	if (p == MAP_FAILED) {
		perror("mmap");
		return 1;
	}
	printf("mapped %s off=0x%lx\n", bar, off);
	for (i = 0; i < 50; i++) {
		printf("magic=0x%x seq=%u cmd=%u status=%u\n", p[0], p[1], p[2], p[3]);
		if (p[0] == MAGIC && p[2] == 1) {
			p[3] = 2;
			printf("acked ping seq=%u\n", p[1]);
			return 0;
		}
		usleep(100000);
	}
	return 2;
}
