// SPDX-License-Identifier: MIT
/* hostpeek [nwords] -- dump the start of /dev/vca_hostbuf from the host */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
int main(int argc, char **argv)
{
	int n = argc > 1 ? atoi(argv[1]) : 12;
	int fd = open("/dev/vca_hostbuf", O_RDWR);
	unsigned int *p; int i;
	if (fd < 0) { perror("open"); return 1; }
	p = mmap(NULL, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (p == MAP_FAILED) { perror("mmap"); return 1; }
	for (i = 0; i < n; i++) printf("[%2d] %08x\n", i, p[i]);
	munmap(p, 0x1000); close(fd); return 0;
}
