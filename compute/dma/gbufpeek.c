// SPDX-License-Identifier: MIT
/* gbufpeek [nwords] -- mmap /dev/vca_gbuf (from alut_gmap do_alloc=1) and
 * print the first words, plus a count of non-zero words over the region.
 * Used to check whether a remote writer actually landed data in this node's
 * buffer. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	size_t len = 0x400000;
	int n = argc > 1 ? atoi(argv[1]) : 8;
	int fd = open("/dev/vca_gbuf", O_RDWR);
	unsigned int *p;
	size_t i, nz = 0;

	if (fd < 0) { perror("open /dev/vca_gbuf"); return 1; }
	p = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (p == MAP_FAILED) { perror("mmap"); return 1; }

	printf("first %d words:", n);
	for (i = 0; i < (size_t)n; i++)
		printf(" %08x", p[i]);
	printf("\n");
	for (i = 0; i < len / 4; i++)
		if (p[i])
			nz++;
	printf("nonzero words: %zu / %zu\n", nz, len / 4);
	if (argc > 2 && !strcmp(argv[2], "clear")) {
		memset(p, 0, len);
		printf("cleared\n");
	}
	munmap(p, len);
	close(fd);
	return 0;
}
