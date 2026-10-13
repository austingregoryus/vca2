// SPDX-License-Identifier: MIT
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
int main(int argc, char **argv)
{
	unsigned long off = strtoul(argv[1], NULL, 0);
	int fd = open("/sys/bus/pci/devices/0000:05:00.0/resource2", O_RDONLY);
	uint32_t *p;
	if (fd < 0) { perror("open"); return 1; }
	p = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, (off_t)off);
	if (p == MAP_FAILED) { perror("mmap"); return 1; }
	printf("host bar2 off=0x%lx magic=0x%x seq=%u cmd=%u status=%u\n",
	       off, p[0], p[1], p[2], p[3]);
	return 0;
}
