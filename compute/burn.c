// SPDX-License-Identifier: MIT
/*
 * burn [seconds] [threads]  -- AVX2 FMA power virus for the Xeon side.
 * Pure register FMA chains: no memory traffic, so this is the realistic
 * worst-case CPU power draw, which is what a passive card's shroud has to
 * be sized against.
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <omp.h>
#include <immintrin.h>

int main(int argc, char **argv) {
    double secs = argc > 1 ? atof(argv[1]) : 60.0;
    int nthr = argc > 2 ? atoi(argv[2]) : 8;
    volatile double sink = 0;
    struct timespec t0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    omp_set_num_threads(nthr);
#pragma omp parallel reduction(+:sink)
    {
        __m256 a = _mm256_set1_ps(1.0000001f);
        __m256 b = _mm256_set1_ps(0.9999999f);
        __m256 c0 = _mm256_set1_ps(0.5f), c1 = _mm256_set1_ps(0.25f);
        __m256 c2 = _mm256_set1_ps(0.125f), c3 = _mm256_set1_ps(0.0625f);
        __m256 c4 = _mm256_set1_ps(0.3f), c5 = _mm256_set1_ps(0.7f);
        struct timespec now;
        long iter = 0;
        for (;;) {
            int i;
            for (i = 0; i < 20000; i++) {
                c0 = _mm256_fmadd_ps(a, c0, b);
                c1 = _mm256_fmadd_ps(a, c1, b);
                c2 = _mm256_fmadd_ps(a, c2, b);
                c3 = _mm256_fmadd_ps(a, c3, b);
                c4 = _mm256_fmadd_ps(a, c4, b);
                c5 = _mm256_fmadd_ps(a, c5, b);
            }
            iter++;
            if ((iter & 3) == 0) {
                double el;
                clock_gettime(CLOCK_MONOTONIC, &now);
                el = (now.tv_sec - t0.tv_sec) + (now.tv_nsec - t0.tv_nsec) * 1e-9;
                if (el >= secs) break;
            }
        }
        {   float tmp[8];
            _mm256_storeu_ps(tmp, _mm256_add_ps(_mm256_add_ps(c0, c1),
                                  _mm256_add_ps(_mm256_add_ps(c2, c3),
                                                _mm256_add_ps(c4, c5))));
            sink += tmp[0] + tmp[7];
        }
    }
    printf("burn done sink=%g\n", (double)sink);
    return 0;
}
