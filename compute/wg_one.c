// SPDX-License-Identifier: MIT
/* wg_one n row0 rows l0 l1  -> one GEMM tile, prints GFLOP/s. l0=l1=0 => local NULL */
#define CL_USE_DEPRECATED_OPENCL_1_2_APIS
#include <CL/cl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static const char *src =
"__kernel void gemm_rc(int n, int rows, __global const float *A,\n"
"                      __global const float *B, __global float *C) {\n"
"  int r = get_global_id(0);\n"
"  int c = get_global_id(1);\n"
"  if (r >= rows || c >= n) return;\n"
"  float s = 0;\n"
"  for (int k = 0; k < n; k++) s += A[r * n + k] * B[k * n + c];\n"
"  C[r * n + c] = s;\n"
"}\n";

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

#define CHK(x, msg) do { if ((x) != CL_SUCCESS) { \
    fprintf(stderr, "FAIL %s err=%d\n", msg, (int)(x)); exit(1);} } while (0)

int main(int argc, char **argv) {
    cl_platform_id p; cl_device_id d; cl_int err;
    cl_context ctx; cl_command_queue q; cl_program prog; cl_kernel kk;
    cl_mem bA, bB, bC;
    float *hA, *hB, *hC;
    size_t asz, bsz, csz, i, g[2], l[2], *lp = NULL;
    int n, row0, rows, reps = 3, it, ok;
    size_t l0, l1;
    double best = 1e30, t0, dt;

    if (argc < 6) { fprintf(stderr, "usage: wg_one n row0 rows l0 l1\n"); return 2; }
    n = atoi(argv[1]); row0 = atoi(argv[2]); rows = atoi(argv[3]);
    l0 = (size_t)atoi(argv[4]); l1 = (size_t)atoi(argv[5]);
    (void)row0;

    CHK(clGetPlatformIDs(1, &p, NULL), "plat");
    CHK(clGetDeviceIDs(p, CL_DEVICE_TYPE_GPU, 1, &d, NULL), "dev");
    ctx = clCreateContext(NULL, 1, &d, NULL, NULL, &err); CHK(err, "ctx");
    q = clCreateCommandQueue(ctx, d, 0, &err); CHK(err, "q");
    prog = clCreateProgramWithSource(ctx, 1, &src, NULL, &err); CHK(err, "prog");
    if (clBuildProgram(prog, 1, &d, "", NULL, NULL) != CL_SUCCESS) { fprintf(stderr, "build fail\n"); return 1; }
    kk = clCreateKernel(prog, "gemm_rc", &err); CHK(err, "kern");

    asz = (size_t)rows * n * 4; bsz = (size_t)n * n * 4; csz = asz;
    hA = malloc(asz); hB = malloc(bsz); hC = malloc(csz);
    if (!hA || !hB || !hC) { fprintf(stderr, "malloc fail\n"); return 1; }
    for (i = 0; i < asz / 4; i++) hA[i] = 1.0f;
    for (i = 0; i < bsz / 4; i++) hB[i] = 1.0f;
    bA = clCreateBuffer(ctx, CL_MEM_READ_ONLY, asz, NULL, &err); CHK(err, "bA");
    bB = clCreateBuffer(ctx, CL_MEM_READ_ONLY, bsz, NULL, &err); CHK(err, "bB");
    bC = clCreateBuffer(ctx, CL_MEM_READ_WRITE, csz, NULL, &err); CHK(err, "bC");
    CHK(clEnqueueWriteBuffer(q, bA, CL_TRUE, 0, asz, hA, 0, NULL, NULL), "wA");
    CHK(clEnqueueWriteBuffer(q, bB, CL_TRUE, 0, bsz, hB, 0, NULL, NULL), "wB");
    CHK(clSetKernelArg(kk, 0, sizeof(int), &n), "a0");
    CHK(clSetKernelArg(kk, 1, sizeof(int), &rows), "a1");
    CHK(clSetKernelArg(kk, 2, sizeof(cl_mem), &bA), "a2");
    CHK(clSetKernelArg(kk, 3, sizeof(cl_mem), &bB), "a3");
    CHK(clSetKernelArg(kk, 4, sizeof(cl_mem), &bC), "a4");

    g[0] = rows; g[1] = n;
    if (l0 && l1) {
        l[0] = l0; l[1] = l1; lp = l;
        g[0] = (g[0] + l0 - 1) / l0 * l0;
        g[1] = (g[1] + l1 - 1) / l1 * l1;
    }
    CHK(clEnqueueNDRangeKernel(q, kk, 2, NULL, g, lp, 0, NULL, NULL), "warm");
    clFinish(q);
    for (it = 0; it < reps; it++) {
        t0 = now_s();
        CHK(clEnqueueNDRangeKernel(q, kk, 2, NULL, g, lp, 0, NULL, NULL), "ndr");
        clFinish(q);
        dt = now_s() - t0;
        if (dt < best) best = dt;
    }
    CHK(clEnqueueReadBuffer(q, bC, CL_TRUE, 0, csz, hC, 0, NULL, NULL), "rd");
    ok = (hC[0] == (float)n) && (hC[(size_t)rows * n - 1] == (float)n);
    printf("gflops=%.2f ms=%.1f rows=%d c0=%.1f expect=%.1f %s\n",
           2.0 * rows * n * (double)n / best / 1e9, best * 1e3, rows,
           hC[0], (float)n, ok ? "ok" : "BAD");
    return 0;
}
