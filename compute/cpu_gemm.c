// SPDX-License-Identifier: MIT
/*
 * cpu_gemm [n] [threads] [reps]
 *
 * Blocked OpenMP SGEMM on the Xeon side, reported in GFLOP/s. This is the CPU
 * counterpart to the tuned GPU GEMM, and unlike burn.c (a pure register FMA
 * power virus that measures nothing) it produces a number you can compare.
 *
 * Its real purpose here is a compiler A/B: the stock node image ships gcc 5.4
 * (2016) and a modern image ships gcc 11. Same silicon, same kernel, so any
 * difference is codegen and libm.
 *
 * Verified against a reference so a fast-but-wrong result cannot pass.
 */
#include <math.h>
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define BS 64   /* cache block */

static double now_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static void gemm_blocked(int n, const float *A, const float *B, float *C)
{
#pragma omp parallel for schedule(static)
    for (int ii = 0; ii < n; ii += BS) {
        for (int kk = 0; kk < n; kk += BS) {
            for (int jj = 0; jj < n; jj += BS) {
                int imax = ii + BS < n ? ii + BS : n;
                int kmax = kk + BS < n ? kk + BS : n;
                int jmax = jj + BS < n ? jj + BS : n;
                for (int i = ii; i < imax; i++) {
                    for (int k = kk; k < kmax; k++) {
                        float a = A[(size_t)i * n + k];
                        const float *b = &B[(size_t)k * n];
                        float *c = &C[(size_t)i * n];
                        for (int j = jj; j < jmax; j++)
                            c[j] += a * b[j];
                    }
                }
            }
        }
    }
}

int main(int argc, char **argv)
{
    int n    = argc > 1 ? atoi(argv[1]) : 1024;
    int thr  = argc > 2 ? atoi(argv[2]) : 0;
    int reps = argc > 3 ? atoi(argv[3]) : 3;
    size_t sz = (size_t)n * n;
    float *A = aligned_alloc(64, sz * sizeof(float));
    float *B = aligned_alloc(64, sz * sizeof(float));
    float *C = aligned_alloc(64, sz * sizeof(float));
    double best = 1e30;
    unsigned seed = 12345;

    if (!A || !B || !C) { fprintf(stderr, "alloc failed\n"); return 1; }
    if (thr > 0) omp_set_num_threads(thr);

    /* Small integers keep the reference exact in float. */
    for (size_t i = 0; i < sz; i++) {
        seed = seed * 1103515245u + 12345u; A[i] = (float)((seed >> 16) % 5) - 2.0f;
        seed = seed * 1103515245u + 12345u; B[i] = (float)((seed >> 16) % 5) - 2.0f;
    }

    for (int r = 0; r < reps; r++) {
        memset(C, 0, sz * sizeof(float));
        double t0 = now_s();
        gemm_blocked(n, A, B, C);
        double dt = now_s() - t0;
        if (dt < best) best = dt;
    }

    /* Verify a few elements against a direct dot product. */
    long bad = 0;
    for (int s = 0; s < 8; s++) {
        int i = (s * 9781) % n, j = (s * 6151 + 3) % n;
        double ref = 0;
        for (int k = 0; k < n; k++)
            ref += (double)A[(size_t)i * n + k] * B[(size_t)k * n + j];
        if (fabs(ref - C[(size_t)i * n + j]) > 1e-3 * (fabs(ref) + 1.0)) bad++;
    }

    printf("cpu_gemm n=%d threads=%d  %.2f GFLOP/s  %.3f s  %s\n",
           n, omp_get_max_threads(),
           2.0 * n * n * (double)n / best / 1e9, best,
           bad ? "*** WRONG ***" : "verified");
    return bad ? 1 : 0;
}
