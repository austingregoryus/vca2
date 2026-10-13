// SPDX-License-Identifier: MIT
/*
 * VCA2 GEMM workgroup-shape test.
 *
 * Hypothesis under test: the "N multiple of 384" cliff in docs/archive/SIX_DEVICES.md
 * is not a k-loop / problem-N effect. It is the implicit local work size.
 * Every GEMM in cl_daemon.c passes local=NULL with g[0]=rows, and dim 0 is
 * the SIMD-adjacent dimension. When rows factors cleanly (rows = n/3 a
 * multiple of 128, i.e. n a multiple of 384), NEO picks a wide dim-0 group
 * and A[r*n+k] becomes a stride-n gather across lanes.
 *
 * Read-only userspace OpenCL. Touches no VCA driver, debugfs or BAR.
 */
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
"}\n"
"__kernel void gemm_cr(int n, int rows, __global const float *A,\n"
"                      __global const float *B, __global float *C) {\n"
"  int c = get_global_id(0);\n"
"  int r = get_global_id(1);\n"
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

static cl_context ctx;
static cl_command_queue q;
static cl_kernel k_rc, k_cr;
static cl_mem bA, bB, bC;
static float *hA, *hB, *hC;
static int cap_rows, cap_n;

#define CHK(x, msg) do { if ((x) != CL_SUCCESS) { \
    fprintf(stderr, "FAIL %s err=%d line %d\n", msg, (int)(x), __LINE__); exit(1);} } while (0)

static void ensure(int n, int rows) {
    cl_int err;
    size_t asz, bsz, csz, i;
    if (n == cap_n && rows == cap_rows) return;
    if (bA) { clReleaseMemObject(bA); clReleaseMemObject(bB); clReleaseMemObject(bC); }
    free(hA); free(hB); free(hC);
    asz = (size_t)rows * n * 4; bsz = (size_t)n * n * 4; csz = asz;
    hA = malloc(asz); hB = malloc(bsz); hC = malloc(csz);
    if (!hA || !hB || !hC) { fprintf(stderr, "FAIL malloc\n"); exit(1); }
    for (i = 0; i < asz / 4; i++) hA[i] = 1.0f;
    for (i = 0; i < bsz / 4; i++) hB[i] = 1.0f;
    bA = clCreateBuffer(ctx, CL_MEM_READ_ONLY, asz, NULL, &err); CHK(err, "bufA");
    bB = clCreateBuffer(ctx, CL_MEM_READ_ONLY, bsz, NULL, &err); CHK(err, "bufB");
    bC = clCreateBuffer(ctx, CL_MEM_READ_WRITE, csz, NULL, &err); CHK(err, "bufC");
    CHK(clEnqueueWriteBuffer(q, bA, CL_TRUE, 0, asz, hA, 0, NULL, NULL), "wA");
    CHK(clEnqueueWriteBuffer(q, bB, CL_TRUE, 0, bsz, hB, 0, NULL, NULL), "wB");
    cap_n = n; cap_rows = rows;
}

/* l0/l1 == 0 means pass local=NULL (let the runtime choose) */
static void run(const char *tag, int swapped, int n, int rows, size_t l0, size_t l1) {
    cl_kernel kk = swapped ? k_cr : k_rc;
    size_t g[2], l[2];
    size_t *lp = NULL;
    int reps = 3, i, ok;
    double best = 1e30, flops, dt, t0;
    cl_int err;

    ensure(n, rows);
    CHK(clSetKernelArg(kk, 0, sizeof(int), &n), "a0");
    CHK(clSetKernelArg(kk, 1, sizeof(int), &rows), "a1");
    CHK(clSetKernelArg(kk, 2, sizeof(cl_mem), &bA), "a2");
    CHK(clSetKernelArg(kk, 3, sizeof(cl_mem), &bB), "a3");
    CHK(clSetKernelArg(kk, 4, sizeof(cl_mem), &bC), "a4");

    if (swapped) { g[0] = n; g[1] = rows; } else { g[0] = rows; g[1] = n; }
    if (l0 && l1) {
        l[0] = l0; l[1] = l1; lp = l;
        g[0] = (g[0] + l0 - 1) / l0 * l0;
        g[1] = (g[1] + l1 - 1) / l1 * l1;
    }

    err = clEnqueueNDRangeKernel(q, kk, 2, NULL, g, lp, 0, NULL, NULL);
    if (err != CL_SUCCESS) {
        printf("  %-34s n=%-5d rows=%-4d  ENQUEUE_ERR %d\n", tag, n, rows, (int)err);
        fflush(stdout);
        return;
    }
    clFinish(q);

    for (i = 0; i < reps; i++) {
        t0 = now_s();
        CHK(clEnqueueNDRangeKernel(q, kk, 2, NULL, g, lp, 0, NULL, NULL), "ndr");
        clFinish(q);
        dt = now_s() - t0;
        if (dt < best) best = dt;
    }
    CHK(clEnqueueReadBuffer(q, bC, CL_TRUE, 0, (size_t)rows * n * 4, hC, 0, NULL, NULL), "rd");

    flops = 2.0 * rows * n * (double)n;
    ok = (hC[0] == (float)n) && (hC[(size_t)rows * n - 1] == (float)n);
    printf("  %-34s n=%-5d rows=%-4d  %7.2f GFLOP/s  %7.1f ms  %s\n",
           tag, n, rows, flops / best / 1e9, best * 1e3, ok ? "ok" : "BAD");
    fflush(stdout);
}

int main(void) {
    cl_platform_id p; cl_device_id d; cl_int err;
    cl_program prog;
    char nm[128];
    size_t mwg = 0, pref = 0, kwg = 0;
    cl_uint cu = 0;

    memset(nm, 0, sizeof(nm));
    CHK(clGetPlatformIDs(1, &p, NULL), "plat");
    CHK(clGetDeviceIDs(p, CL_DEVICE_TYPE_GPU, 1, &d, NULL), "dev");
    clGetDeviceInfo(d, CL_DEVICE_NAME, sizeof(nm), nm, NULL);
    clGetDeviceInfo(d, CL_DEVICE_MAX_WORK_GROUP_SIZE, sizeof(mwg), &mwg, NULL);
    clGetDeviceInfo(d, CL_DEVICE_MAX_COMPUTE_UNITS, sizeof(cu), &cu, NULL);
    printf("device: %s  CUs=%u  max_wg=%zu\n", nm, (unsigned)cu, mwg);

    ctx = clCreateContext(NULL, 1, &d, NULL, NULL, &err); CHK(err, "ctx");
    q = clCreateCommandQueue(ctx, d, 0, &err); CHK(err, "queue");
    prog = clCreateProgramWithSource(ctx, 1, &src, NULL, &err); CHK(err, "prog");
    if (clBuildProgram(prog, 1, &d, "", NULL, NULL) != CL_SUCCESS) {
        char log[8192];
        memset(log, 0, sizeof(log));
        clGetProgramBuildInfo(prog, d, CL_PROGRAM_BUILD_LOG, sizeof(log), log, NULL);
        fprintf(stderr, "build log:\n%s\n", log);
        exit(1);
    }
    k_rc = clCreateKernel(prog, "gemm_rc", &err); CHK(err, "k_rc");
    k_cr = clCreateKernel(prog, "gemm_cr", &err); CHK(err, "k_cr");
    clGetKernelWorkGroupInfo(k_rc, d, CL_KERNEL_PREFERRED_WORK_GROUP_SIZE_MULTIPLE, sizeof(pref), &pref, NULL);
    clGetKernelWorkGroupInfo(k_rc, d, CL_KERNEL_WORK_GROUP_SIZE, sizeof(kwg), &kwg, NULL);
    printf("kernel gemm_rc: preferred_wg_multiple=%zu  max_wg=%zu\n\n", pref, kwg);

    printf("== A. the decisive pair: same n, same k-loop, rows differs by 1 ==\n");
    run("rows=512 local=NULL (their slow)", 0, 1536, 512, 0, 0);
    run("rows=511 local=NULL", 0, 1536, 511, 0, 0);
    run("rows=513 local=NULL", 0, 1536, 513, 0, 0);

    printf("\n== B. same rows=512, forced local sizes ==\n");
    run("rows=512 local={1,256}", 0, 1536, 512, 1, 256);
    run("rows=512 local={1,128}", 0, 1536, 512, 1, 128);
    run("rows=512 local={2,128}", 0, 1536, 512, 2, 128);
    run("rows=512 local={16,16}", 0, 1536, 512, 16, 16);
    run("rows=512 local={256,1} (worst)", 0, 1536, 512, 256, 1);

    printf("\n== C. swapped indexing (c = gid0), local=NULL ==\n");
    run("SWAPPED rows=512 local=NULL", 1, 1536, 512, 0, 0);
    run("SWAPPED rows=512 local={256,1}", 1, 1536, 512, 256, 1);

    printf("\n== D. controls at their fast size n=1408 ==\n");
    run("rows=469 local=NULL (their peak)", 0, 1408, 469, 0, 0);
    run("rows=512 local=NULL", 0, 1408, 512, 0, 0);
    run("SWAPPED rows=469 local=NULL", 1, 1408, 469, 0, 0);
    run("SWAPPED rows=469 local={256,1}", 1, 1408, 469, 256, 1);

    printf("\n== E. n=384, the original poison size ==\n");
    run("rows=128 local=NULL (their slow)", 0, 384, 128, 0, 0);
    run("rows=127 local=NULL", 0, 384, 127, 0, 0);
    run("SWAPPED rows=128 local={256,1}", 1, 384, 128, 256, 1);

    printf("\nDONE\n");
    return 0;
}
