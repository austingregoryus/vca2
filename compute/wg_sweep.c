// SPDX-License-Identifier: MIT
/*
 * Follow-up to wg_test: find the best explicit local work size and check
 * whether one choice removes the size dependence entirely.
 * Also re-checks CL_DEVICE_MAX_COMPUTE_UNITS (GPU.md claims 8; wg_test saw 72).
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
"}\n";

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static cl_context ctx;
static cl_command_queue q;
static cl_kernel kk;
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

static double one(int n, int rows, size_t l0, size_t l1, int *okout) {
    size_t g[2], l[2];
    size_t *lp = NULL;
    int reps = 3, i;
    double best = 1e30, dt, t0;
    cl_int err;

    ensure(n, rows);
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
    err = clEnqueueNDRangeKernel(q, kk, 2, NULL, g, lp, 0, NULL, NULL);
    if (err != CL_SUCCESS) { *okout = -1; return 0; }
    clFinish(q);
    for (i = 0; i < reps; i++) {
        t0 = now_s();
        CHK(clEnqueueNDRangeKernel(q, kk, 2, NULL, g, lp, 0, NULL, NULL), "ndr");
        clFinish(q);
        dt = now_s() - t0;
        if (dt < best) best = dt;
    }
    CHK(clEnqueueReadBuffer(q, bC, CL_TRUE, 0, (size_t)rows * n * 4, hC, 0, NULL, NULL), "rd");
    *okout = (hC[0] == (float)n) && (hC[(size_t)rows * n - 1] == (float)n);
    return 2.0 * rows * n * (double)n / best / 1e9;
}

int main(void) {
    cl_platform_id p; cl_device_id d; cl_int err;
    cl_program prog;
    char nm[128], ver[128];
    size_t mwg = 0, pref = 0;
    cl_uint cu = 0, freq = 0;
    int ns[] = {384, 1024, 1408, 1536, 1600, 2048};
    size_t cand[][2] = {{1,32},{1,64},{1,128},{1,256},{2,64},{2,128},{4,64},{8,32},{16,16},{32,8},{64,4}};
    int ni, ci, ok;
    int nn = (int)(sizeof(ns)/sizeof(ns[0]));
    int nc = (int)(sizeof(cand)/sizeof(cand[0]));

    memset(nm, 0, sizeof(nm)); memset(ver, 0, sizeof(ver));
    CHK(clGetPlatformIDs(1, &p, NULL), "plat");
    CHK(clGetDeviceIDs(p, CL_DEVICE_TYPE_GPU, 1, &d, NULL), "dev");
    clGetDeviceInfo(d, CL_DEVICE_NAME, sizeof(nm), nm, NULL);
    clGetDeviceInfo(d, CL_DEVICE_VERSION, sizeof(ver), ver, NULL);
    clGetDeviceInfo(d, CL_DEVICE_MAX_WORK_GROUP_SIZE, sizeof(mwg), &mwg, NULL);
    clGetDeviceInfo(d, CL_DEVICE_MAX_COMPUTE_UNITS, sizeof(cu), &cu, NULL);
    clGetDeviceInfo(d, CL_DEVICE_MAX_CLOCK_FREQUENCY, sizeof(freq), &freq, NULL);
    printf("device: %s\nversion: %s\nMAX_COMPUTE_UNITS=%u  MAX_CLOCK=%u MHz  max_wg=%zu\n",
           nm, ver, (unsigned)cu, (unsigned)freq, mwg);
    printf("FP32 peak estimate (CU*8lanes*2flop*clk) = %.0f GFLOP/s\n\n",
           cu * 8.0 * 2.0 * freq / 1000.0);

    ctx = clCreateContext(NULL, 1, &d, NULL, NULL, &err); CHK(err, "ctx");
    q = clCreateCommandQueue(ctx, d, 0, &err); CHK(err, "queue");
    prog = clCreateProgramWithSource(ctx, 1, &src, NULL, &err); CHK(err, "prog");
    if (clBuildProgram(prog, 1, &d, "", NULL, NULL) != CL_SUCCESS) { fprintf(stderr, "build fail\n"); exit(1); }
    kk = clCreateKernel(prog, "gemm_rc", &err); CHK(err, "kern");
    clGetKernelWorkGroupInfo(kk, d, CL_KERNEL_PREFERRED_WORK_GROUP_SIZE_MULTIPLE, sizeof(pref), &pref, NULL);
    printf("preferred_wg_multiple=%zu\n\n", pref);

    printf("Local-size sweep. rows = n/3 (the split cl_daemon.c uses).\n");
    printf("%-10s", "local");
    for (ni = 0; ni < nn; ni++) printf("  n=%-6d", ns[ni]);
    printf("\n");

    printf("%-10s", "NULL");
    for (ni = 0; ni < nn; ni++) {
        double g = one(ns[ni], ns[ni] / 3, 0, 0, &ok);
        printf("  %7.1f%s", g, ok == 1 ? " " : "!");
    }
    printf("\n");

    for (ci = 0; ci < nc; ci++) {
        char lab[24];
        snprintf(lab, sizeof(lab), "{%zu,%zu}", cand[ci][0], cand[ci][1]);
        printf("%-10s", lab);
        for (ni = 0; ni < nn; ni++) {
            double g = one(ns[ni], ns[ni] / 3, cand[ci][0], cand[ci][1], &ok);
            if (ok < 0) printf("  %7s ", "err");
            else printf("  %7.1f%s", g, ok == 1 ? " " : "!");
        }
        printf("\n");
        fflush(stdout);
    }

    printf("\nDONE\n");
    return 0;
}
