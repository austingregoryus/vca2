// SPDX-License-Identifier: MIT
/* mmap /dev/vca_alut_* and poke the magic/seq header. */
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
	const char *path = argc > 1 ? argv[1] : "/dev/vca_alut_0000_05_00_0";
	int fd = open(path, O_RDWR);
	uint32_t *p;
	if (fd < 0) {
		perror(path);
		return 1;
	}
	p = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (p == MAP_FAILED) {
		perror("mmap");
		return 1;
	}
	printf("magic=0x%x seq=%u cmd=%u status=%u\n", p[0], p[1], p[2], p[3]);
	if (p[0] != MAGIC)
		fprintf(stderr, "WARN magic\n");
	p[1] = p[1] + 1;
	p[2] = 1; /* ping */
	p[3] = 0;
	msync(p, 4096, MS_SYNC);
	printf("wrote seq=%u cmd=1\n", p[1]);
	return 0;
}
