// SPDX-License-Identifier: MIT
/* Persistent OpenCL SAXPY server. Context stays warm. TCP 9200.
 * Commands (one line):
 *   SAXPY N ITERS BASE
 *   BURN N ITERS BASE SECS
 *   PING
 *   QUIT
 */
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <netinet/in.h>
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

typedef int cl_int;
typedef unsigned int cl_uint;
typedef unsigned long cl_ulong;
typedef cl_ulong cl_bitfield;
typedef void *cl_platform_id;
typedef void *cl_device_id;
typedef void *cl_context;
typedef void *cl_command_queue;
typedef void *cl_program;
typedef void *cl_kernel;
typedef void *cl_mem;

#define CL_SUCCESS 0
#define CL_DEVICE_TYPE_GPU (1u << 2)
#define CL_MEM_READ_WRITE (1u << 0)
#define CL_MEM_COPY_HOST_PTR (1u << 5)
#define CL_MEM_READ_ONLY (1u << 2)
#define PORT 9200
#define MAX_N (16 * 1024 * 1024)

typedef cl_int (*pGetPlatformIDs)(cl_uint, cl_platform_id *, cl_uint *);
typedef cl_int (*pGetDeviceIDs)(cl_platform_id, cl_bitfield, cl_uint, cl_device_id *, cl_uint *);
typedef cl_int (*pGetDeviceInfo)(cl_device_id, cl_uint, size_t, void *, size_t *);
typedef cl_context (*pCreateContext)(void *, cl_uint, const cl_device_id *, void *, void *, cl_int *);
typedef cl_command_queue (*pCreateCommandQueue)(cl_context, cl_device_id, cl_ulong, cl_int *);
typedef cl_program (*pCreateProgramWithSource)(cl_context, cl_uint, const char **, const size_t *, cl_int *);
typedef cl_int (*pBuildProgram)(cl_program, cl_uint, const cl_device_id *, const char *, void *, void *);
typedef cl_kernel (*pCreateKernel)(cl_program, const char *, cl_int *);
typedef cl_mem (*pCreateBuffer)(cl_context, cl_bitfield, size_t, void *, cl_int *);
typedef cl_int (*pReleaseMem)(cl_mem);
typedef cl_int (*pSetKernelArg)(cl_kernel, cl_uint, size_t, const void *);
typedef cl_int (*pEnqueueNDRange)(cl_command_queue, cl_kernel, cl_uint, const size_t *, const size_t *, const size_t *, cl_uint, const void *, void *);
typedef cl_int (*pEnqueueWrite)(cl_command_queue, cl_mem, cl_uint, size_t, size_t, const void *, cl_uint, const void *, void *);
typedef cl_int (*pEnqueueRead)(cl_command_queue, cl_mem, cl_uint, size_t, size_t, void *, cl_uint, const void *, void *);
typedef cl_int (*pFinish)(cl_command_queue);

static pGetPlatformIDs GetPlatformIDs;
static pGetDeviceIDs GetDeviceIDs;
static pGetDeviceInfo GetDeviceInfo;
static pCreateContext CreateContext;
static pCreateCommandQueue CreateCommandQueue;
static pCreateProgramWithSource CreateProgramWithSource;
static pBuildProgram BuildProgram;
static pCreateKernel CreateKernel;
static pCreateBuffer CreateBuffer;
static pReleaseMem ReleaseMem;
static pSetKernelArg SetKernelArg;
static pEnqueueNDRange EnqueueNDRange;
static pEnqueueWrite EnqueueWrite;
static pEnqueueRead EnqueueRead;
static pFinish Finish;

static cl_context ctx;
static cl_device_id gdev;
static cl_command_queue q;
static cl_kernel k, kgemm, kgemm_b, kgemm_f4, kgemm_lda, kgemm_ldab, kgemm_k16;
static cl_kernel kgemm_fast;
static cl_mem bx, by, bA, bB, bC;
static float *hx, *hy, *hA, *hB, *hC;
static int cap;
static int gemm_n, gemm_rows, gemm_row0;
static int prep_n, prep_base;
static char devname[128];

/*
 * Tuned GEMM (GEMMT). 8 rows per work-item x float2 columns, A staged in
 * __local, dim 0 = column so B loads coalesce across SIMD lanes. ~5.7x the
 * naive kernel (456 vs 79 GFLOP/s at n=1408). See docs/GEMM_WORKGROUP.md.
 *
 * MUST be launched with local = {256, 1}. As[][] is per-workgroup but the
 * row block is get_global_id(1)*8, so a dim-1 local size > 1 races two row
 * blocks onto one tile. That corruption is INVISIBLE to an A=1/B=1/C==n
 * check -- it was caught only against a CPU reference with random data.
 * Built as its own program so a build failure here cannot break the rest.
 */
#define GF_R  8
#define GF_L0 256
static const char *ksrc_fast =
	"__kernel void gemm_fast(int n, int rows, __global const float *A,\n"
	"                        __global const float *B, __global float *C) {\n"
	"  int c2 = get_global_id(0) * 2;\n"
	"  int lc = get_local_id(0);\n"
	"  int ls = get_local_size(0);\n"
	"  int r0 = get_global_id(1) * 8;\n"
	"  __local float As[8][32];\n"
	"  float2 acc[8];\n"
	"  for (int i = 0; i < 8; i++) acc[i] = (float2)(0.0f, 0.0f);\n"
	"  for (int kb = 0; kb < n; kb += 32) {\n"
	"    barrier(CLK_LOCAL_MEM_FENCE);\n"
	"    for (int t = lc; t < 256; t += ls) {\n"
	"      int i = t >> 5, kk = t & 31;\n"
	"      As[i][kk] = (r0+i < rows && kb+kk < n) ? A[(r0+i)*n + kb+kk] : 0.0f;\n"
	"    }\n"
	"    barrier(CLK_LOCAL_MEM_FENCE);\n"
	"    if (c2 < n) {\n"
	"      int kmax = (n - kb) < 32 ? (n - kb) : 32;\n"
	"      for (int kk = 0; kk < kmax; kk++) {\n"
	"        float2 b = vload2(0, B + (kb+kk)*n + c2);\n"
	"        for (int i = 0; i < 8; i++) acc[i] += As[i][kk] * b;\n"
	"      }\n"
	"    }\n"
	"  }\n"
	"  if (c2 < n)\n"
	"    for (int i = 0; i < 8; i++)\n"
	"      if (r0+i < rows) vstore2(acc[i], 0, C + (r0+i)*n + c2);\n"
	"}\n";

static const char *ksrc =
	"__kernel void saxpy(int n, float a, __global const float *x, __global float *y) {\n"
	"  int i = get_global_id(0);\n"
	"  if (i < n) y[i] = a * x[i] + y[i];\n"
	"}\n"
	"__kernel void gemm_rows(int n, int rows, __global const float *A,\n"
	"                        __global const float *B, __global float *C) {\n"
	"  int r = get_global_id(0);\n"
	"  int c = get_global_id(1);\n"
	"  if (r >= rows || c >= n) return;\n"
	"  float s = 0;\n"
	"  int k;\n"
	"  for (k = 0; k < n; k++) s += A[r * n + k] * B[k * n + c];\n"
	"  C[r * n + c] = s;\n"
	"}\n"
	"#define TS 16\n"
	"__kernel void gemm_blocked(int n, int rows, __global const float *A,\n"
	"                           __global const float *B, __global float *C) {\n"
	"  const int row = get_global_id(0);\n"
	"  const int col = get_global_id(1);\n"
	"  const int lr = get_local_id(0);\n"
	"  const int lc = get_local_id(1);\n"
	"  __local float As[TS][TS];\n"
	"  __local float Bs[TS][TS];\n"
	"  float acc = 0;\n"
	"  const int tiles = n / TS;\n"
	"  int t, kk;\n"
	"  for (t = 0; t < tiles; t++) {\n"
	"    As[lr][lc] = (row < rows && (t * TS + lc) < n) ? A[row * n + t * TS + lc] : 0;\n"
	"    Bs[lr][lc] = ((t * TS + lr) < n && col < n) ? B[(t * TS + lr) * n + col] : 0;\n"
	"    barrier(CLK_LOCAL_MEM_FENCE);\n"
	"    for (kk = 0; kk < TS; kk++) acc += As[lr][kk] * Bs[kk][lc];\n"
	"    barrier(CLK_LOCAL_MEM_FENCE);\n"
	"  }\n"
	"  if (row < rows && col < n) C[row * n + col] = acc;\n"
	"}\n"
	"__kernel void gemm_f4(int n, int rows, __global const float *A,\n"
	"                      __global const float *B, __global float *C) {\n"
	"  int r = get_global_id(0);\n"
	"  int c = get_global_id(1);\n"
	"  if (r >= rows || c >= n) return;\n"
	"  float s = 0;\n"
	"  int k;\n"
	"  __global const float *ar = A + r * n;\n"
	"  for (k = 0; k < n; k += 4) {\n"
	"    float4 a = vload4(0, ar + k);\n"
	"    s += a.x * B[(k + 0) * n + c];\n"
	"    s += a.y * B[(k + 1) * n + c];\n"
	"    s += a.z * B[(k + 2) * n + c];\n"
	"    s += a.w * B[(k + 3) * n + c];\n"
	"  }\n"
	"  C[r * n + c] = s;\n"
	"}\n"
	"__kernel void gemm_lda(int n, int rows, int lda, __global const float *A,\n"
	"                       __global const float *B, __global float *C) {\n"
	"  int r = get_global_id(0);\n"
	"  int c = get_global_id(1);\n"
	"  if (r >= rows || c >= n) return;\n"
	"  float s = 0;\n"
	"  int k;\n"
	"  for (k = 0; k < n; k++) s += A[r * n + k] * B[k * lda + c];\n"
	"  C[r * n + c] = s;\n"
	"}\n"
	"__kernel void gemm_ldab(int n, int rows, int lda, int ldb,\n"
	"                        __global const float *A, __global const float *B,\n"
	"                        __global float *C) {\n"
	"  int r = get_global_id(0);\n"
	"  int c = get_global_id(1);\n"
	"  if (r >= rows || c >= n) return;\n"
	"  float s = 0;\n"
	"  int k;\n"
	"  for (k = 0; k < n; k++) s += A[r * lda + k] * B[k * ldb + c];\n"
	"  C[r * n + c] = s;\n"
	"}\n"
	"__kernel void gemm_k16(int n, int rows, __global const float *A,\n"
	"                       __global const float *B, __global float *C) {\n"
	"  int r = get_global_id(0);\n"
	"  int c = get_global_id(1);\n"
	"  if (r >= rows || c >= n) return;\n"
	"  float s = 0;\n"
	"  int k;\n"
	"  for (k = 0; k + 15 < n; k += 16) {\n"
	"    s += A[r * n + k + 0] * B[(k + 0) * n + c];\n"
	"    s += A[r * n + k + 1] * B[(k + 1) * n + c];\n"
	"    s += A[r * n + k + 2] * B[(k + 2) * n + c];\n"
	"    s += A[r * n + k + 3] * B[(k + 3) * n + c];\n"
	"    s += A[r * n + k + 4] * B[(k + 4) * n + c];\n"
	"    s += A[r * n + k + 5] * B[(k + 5) * n + c];\n"
	"    s += A[r * n + k + 6] * B[(k + 6) * n + c];\n"
	"    s += A[r * n + k + 7] * B[(k + 7) * n + c];\n"
	"    s += A[r * n + k + 8] * B[(k + 8) * n + c];\n"
	"    s += A[r * n + k + 9] * B[(k + 9) * n + c];\n"
	"    s += A[r * n + k + 10] * B[(k + 10) * n + c];\n"
	"    s += A[r * n + k + 11] * B[(k + 11) * n + c];\n"
	"    s += A[r * n + k + 12] * B[(k + 12) * n + c];\n"
	"    s += A[r * n + k + 13] * B[(k + 13) * n + c];\n"
	"    s += A[r * n + k + 14] * B[(k + 14) * n + c];\n"
	"    s += A[r * n + k + 15] * B[(k + 15) * n + c];\n"
	"  }\n"
	"  for (; k < n; k++) s += A[r * n + k] * B[k * n + c];\n"
	"  C[r * n + c] = s;\n"
	"}\n";

static double now_s(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static int ensure_buf(int n)
{
	cl_int err = 0;
	if (n > MAX_N)
		return -1;
	if (n <= cap)
		return 0;
	if (bx)
		ReleaseMem(bx);
	if (by)
		ReleaseMem(by);
	free(hx);
	free(hy);
	hx = malloc((size_t)n * 4);
	hy = malloc((size_t)n * 4);
	if (!hx || !hy)
		return -1;
	bx = CreateBuffer(ctx, CL_MEM_READ_ONLY, (size_t)n * 4, NULL, &err);
	by = CreateBuffer(ctx, CL_MEM_READ_WRITE, (size_t)n * 4, NULL, &err);
	if (err != CL_SUCCESS)
		return -1;
	cap = n;
	return 0;
}

static int do_saxpy(int n, int iters, int base, int extra_loops, char *out, size_t outn)
{
	int i, it, loops;
	float a = 1.5f;
	size_t g;
	double t0, gpu;
	cl_int err;

	if (ensure_buf(n) != 0)
		return -1;
	for (i = 0; i < n; i++) {
		hx[i] = 0.001f * (float)(base + i);
		hy[i] = 1.0f;
	}
	err = EnqueueWrite(q, bx, 1, 0, (size_t)n * 4, hx, 0, NULL, NULL);
	err |= EnqueueWrite(q, by, 1, 0, (size_t)n * 4, hy, 0, NULL, NULL);
	SetKernelArg(k, 0, sizeof(int), &n);
	SetKernelArg(k, 1, sizeof(float), &a);
	SetKernelArg(k, 2, sizeof(cl_mem), &bx);
	SetKernelArg(k, 3, sizeof(cl_mem), &by);
	g = (size_t)n;
	EnqueueNDRange(q, k, 1, NULL, &g, NULL, 0, NULL, NULL);
	Finish(q);

	loops = extra_loops > 0 ? extra_loops : 1;
	t0 = now_s();
	for (it = 0; it < iters * loops; it++)
		EnqueueNDRange(q, k, 1, NULL, &g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, by, 1, 0, (size_t)n * 4, hy, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)n * (double)iters * (double)loops;
		double gflops = gpu > 0 ? flops / gpu / 1e9 : 0;
		snprintf(out, outn,
			 "OK n=%d iters=%d loops=%d base=%d gpu_s=%.4f gflops=%.2f y0=%.6f y100=%.6f ylast=%.6f\n",
			 n, iters, loops, base, gpu, gflops, hy[0], n > 100 ? hy[100] : 0, hy[n - 1]);
	}
	return 0;
}

static int do_kernel_only(int n, int iters, int readback, char *out, size_t outn)
{
	int it;
	size_t g;
	double t0, gpu;
	if (n != prep_n || n <= 0)
		return -1;
	g = (size_t)n;
	t0 = now_s();
	for (it = 0; it < iters; it++)
		EnqueueNDRange(q, k, 1, NULL, &g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	if (readback)
		EnqueueRead(q, by, 1, 0, (size_t)n * 4, hy, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)n * (double)iters;
		snprintf(out, outn,
			 "OK KERNEL n=%d iters=%d gpu_s=%.4f gflops=%.2f y0=%.6f readback=%d\n",
			 n, iters, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 readback ? hy[0] : -1, readback);
	}
	return 0;
}

static int do_prep(int n, int base, char *out, size_t outn)
{
	int i;
	float a = 1.5f;
	size_t g;
	if (ensure_buf(n) != 0)
		return -1;
	for (i = 0; i < n; i++) {
		hx[i] = 0.001f * (float)(base + i);
		hy[i] = 1.0f;
	}
	EnqueueWrite(q, bx, 1, 0, (size_t)n * 4, hx, 0, NULL, NULL);
	EnqueueWrite(q, by, 1, 0, (size_t)n * 4, hy, 0, NULL, NULL);
	SetKernelArg(k, 0, sizeof(int), &n);
	SetKernelArg(k, 1, sizeof(float), &a);
	SetKernelArg(k, 2, sizeof(cl_mem), &bx);
	SetKernelArg(k, 3, sizeof(cl_mem), &by);
	g = (size_t)n;
	EnqueueNDRange(q, k, 1, NULL, &g, NULL, 0, NULL, NULL);
	Finish(q);
	prep_n = n;
	prep_base = base;
	snprintf(out, outn, "OK PREP n=%d base=%d\n", n, base);
	return 0;
}

static int ensure_gemm(int n, int rows)
{
	cl_int err = 0;
	size_t asz = (size_t)rows * n * 4;
	size_t bsz = (size_t)n * n * 4;
	if (n < 8 || rows < 1 || n * rows > MAX_N)
		return -1;
	if (bA)
		ReleaseMem(bA);
	if (bB)
		ReleaseMem(bB);
	if (bC)
		ReleaseMem(bC);
	free(hA);
	free(hB);
	free(hC);
	hA = malloc(asz);
	hB = malloc(bsz);
	hC = malloc(asz);
	if (!hA || !hB || !hC)
		return -1;
	bA = CreateBuffer(ctx, CL_MEM_READ_ONLY, asz, NULL, &err);
	bB = CreateBuffer(ctx, CL_MEM_READ_ONLY, bsz, NULL, &err);
	bC = CreateBuffer(ctx, CL_MEM_READ_WRITE, asz, NULL, &err);
	if (err != CL_SUCCESS)
		return -1;
	gemm_n = n;
	gemm_rows = rows;
	return 0;
}

static int ensure_gemm_lda(int n, int rows, int lda)
{
	cl_int err = 0;
	size_t asz = (size_t)rows * n * 4;
	size_t bsz = (size_t)n * lda * 4;
	if (n < 8 || rows < 1 || lda < n || n * rows > MAX_N)
		return -1;
	if (bA)
		ReleaseMem(bA);
	if (bB)
		ReleaseMem(bB);
	if (bC)
		ReleaseMem(bC);
	free(hA);
	free(hB);
	free(hC);
	hA = malloc(asz);
	hB = malloc(bsz);
	hC = malloc(asz);
	if (!hA || !hB || !hC)
		return -1;
	bA = CreateBuffer(ctx, CL_MEM_READ_ONLY, asz, NULL, &err);
	bB = CreateBuffer(ctx, CL_MEM_READ_ONLY, bsz, NULL, &err);
	bC = CreateBuffer(ctx, CL_MEM_READ_WRITE, asz, NULL, &err);
	if (err != CL_SUCCESS)
		return -1;
	gemm_n = n;
	gemm_rows = rows;
	return 0;
}

/*
 * Explicit local work size for the naive row/col GEMM.
 *
 * dim 0 is r (the row) and dim 0 is also the SIMD-adjacent dimension, so a
 * wide dim-0 group makes A[r*n+k] a stride-n gather across lanes. Passing
 * local=NULL let NEO pick that shape whenever rows factored cleanly, which is
 * the whole "N multiple of 384" cliff: n%384==0 <=> rows==n/3 is a multiple
 * of 128. Pinning dim 0 to 1 removes it. Measured on node 0, n=1536 rows=512:
 * 9.3 GFLOP/s with NULL, 58.2 with {1,128}. See docs/GEMM_WORKGROUP.md.
 */
#define GEMM_L0 1
#define GEMM_L1 128

static void gemm_local(size_t *g, size_t *l)
{
	l[0] = GEMM_L0;
	l[1] = GEMM_L1;
	g[0] = (g[0] + GEMM_L0 - 1) / GEMM_L0 * GEMM_L0;
	g[1] = (g[1] + GEMM_L1 - 1) / GEMM_L1 * GEMM_L1;
}

static int do_gemm(int n, int row0, int rows, char *out, size_t outn)
{
	int i, total;
	size_t g[2], l[2];
	double t0, gpu;
	cl_int err;
	if (ensure_gemm(n, rows) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm, 0, sizeof(int), &n);
	SetKernelArg(kgemm, 1, sizeof(int), &rows);
	SetKernelArg(kgemm, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm, 4, sizeof(cl_mem), &bC);
	g[0] = (size_t)rows;
	g[1] = (size_t)n;
	gemm_local(g, l);
	EnqueueNDRange(q, kgemm, 2, NULL, g, l, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm, 2, NULL, g, l, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)total * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK GEMM n=%d row0=%d rows=%d gpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, row0, rows, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[total - 1], (float)n);
	}
	(void)err;
	gemm_row0 = row0;
	return 0;
}

static int do_gemm_t(int n, int row0, int rows, char *out, size_t outn)
{
	int i, total;
	size_t g[2], l[2];
	double t0, gpu;
	if (!kgemm_fast)
		return -1;
	if (ensure_gemm(n, rows) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm_fast, 0, sizeof(int), &n);
	SetKernelArg(kgemm_fast, 1, sizeof(int), &rows);
	SetKernelArg(kgemm_fast, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm_fast, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm_fast, 4, sizeof(cl_mem), &bC);
	/* dim0 = columns/2, dim1 = row blocks. local dim1 MUST stay 1. */
	l[0] = GF_L0;
	l[1] = 1;
	g[0] = ((size_t)((n + 1) / 2) + GF_L0 - 1) / GF_L0 * GF_L0;
	g[1] = (size_t)((rows + GF_R - 1) / GF_R);
	EnqueueNDRange(q, kgemm_fast, 2, NULL, g, l, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm_fast, 2, NULL, g, l, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)total * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK GEMMT n=%d row0=%d rows=%d gpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, row0, rows, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[total - 1], (float)n);
	}
	gemm_row0 = row0;
	return 0;
}

static int do_gemm_k16(int n, int row0, int rows, char *out, size_t outn)
{
	int i, total;
	size_t g[2];
	double t0, gpu;
	if (ensure_gemm(n, rows) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm_k16, 0, sizeof(int), &n);
	SetKernelArg(kgemm_k16, 1, sizeof(int), &rows);
	SetKernelArg(kgemm_k16, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm_k16, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm_k16, 4, sizeof(cl_mem), &bC);
	g[0] = (size_t)rows;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm_k16, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm_k16, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)total * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK GEMMK16 n=%d row0=%d rows=%d gpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, row0, rows, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[total - 1], (float)n);
	}
	return 0;
}

static int do_gemmg(int n, int row0, int rows, int g1, char *out, size_t outn)
{
	int i, total;
	size_t g[2];
	double t0, gpu;
	if (g1 < n)
		g1 = n;
	if (ensure_gemm(n, rows) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm, 0, sizeof(int), &n);
	SetKernelArg(kgemm, 1, sizeof(int), &rows);
	SetKernelArg(kgemm, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm, 4, sizeof(cl_mem), &bC);
	g[0] = (size_t)rows;
	g[1] = (size_t)g1;
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)total * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK GEMMG n=%d g1=%d row0=%d rows=%d gpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, g1, row0, rows, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[total - 1], (float)n);
	}
	return 0;
}

static int do_info(char *out, size_t outn)
{
	size_t wg = 0, wis[3] = {0, 0, 0};
	cl_uint cu = 0, vfloat = 0;
	cl_ulong loc = 0, glob = 0;
	GetDeviceInfo(gdev, 0x1002, sizeof(cu), &cu, NULL); /* MAX_COMPUTE_UNITS; 0x1021 was MAX_CONSTANT_ARGS (8) */
	GetDeviceInfo(gdev, 0x1004, sizeof(wg), &wg, NULL);
	GetDeviceInfo(gdev, 0x1005, sizeof(wis), wis, NULL);
	GetDeviceInfo(gdev, 0x1023, sizeof(loc), &loc, NULL); /* LOCAL_MEM_SIZE; 0x1022 was LOCAL_MEM_TYPE (the bogus "local=1") */
	GetDeviceInfo(gdev, 0x101F, sizeof(glob), &glob, NULL);
	GetDeviceInfo(gdev, 0x100A, sizeof(vfloat), &vfloat, NULL);
	snprintf(out, outn,
		 "OK INFO cu=%u max_wg=%zu wis=%zu,%zu,%zu local=%lu glob=%lu pref_float=%u\n",
		 cu, wg, wis[0], wis[1], wis[2], (unsigned long)loc, (unsigned long)glob, vfloat);
	return 0;
}

static int do_gemm_lda(int n, int row0, int rows, int lda, char *out, size_t outn)
{
	int i, k, c, total;
	size_t g[2];
	double t0, gpu;
	if (ensure_gemm_lda(n, rows, lda) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * lda; i++)
		hB[i] = 0.0f;
	for (k = 0; k < n; k++)
		for (c = 0; c < n; c++)
			hB[k * lda + c] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * lda * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm_lda, 0, sizeof(int), &n);
	SetKernelArg(kgemm_lda, 1, sizeof(int), &rows);
	SetKernelArg(kgemm_lda, 2, sizeof(int), &lda);
	SetKernelArg(kgemm_lda, 3, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm_lda, 4, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm_lda, 5, sizeof(cl_mem), &bC);
	g[0] = (size_t)rows;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm_lda, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm_lda, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)total * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK GEMMLDA n=%d lda=%d row0=%d rows=%d gpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, lda, row0, rows, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[total - 1], (float)n);
	}
	return 0;
}

static int do_gemm_ldab(int n, int row0, int rows, int lda, int ldb, char *out, size_t outn)
{
	int i, r, k, c, total;
	size_t g[2];
	double t0, gpu;
	cl_int err = 0;
	size_t asz, bsz, csz;
	if (lda < n || ldb < n || n < 8 || rows < 1)
		return -1;
	asz = (size_t)rows * lda * 4;
	bsz = (size_t)n * ldb * 4;
	csz = (size_t)rows * n * 4;
	if (bA)
		ReleaseMem(bA);
	if (bB)
		ReleaseMem(bB);
	if (bC)
		ReleaseMem(bC);
	free(hA);
	free(hB);
	free(hC);
	hA = malloc(asz);
	hB = malloc(bsz);
	hC = malloc(csz);
	if (!hA || !hB || !hC)
		return -1;
	bA = CreateBuffer(ctx, CL_MEM_READ_ONLY, asz, NULL, &err);
	bB = CreateBuffer(ctx, CL_MEM_READ_ONLY, bsz, NULL, &err);
	bC = CreateBuffer(ctx, CL_MEM_READ_WRITE, csz, NULL, &err);
	if (err != CL_SUCCESS)
		return -1;
	total = rows * n;
	for (i = 0; i < (int)(asz / 4); i++)
		hA[i] = 0;
	for (r = 0; r < rows; r++)
		for (k = 0; k < n; k++)
			hA[r * lda + k] = 1.0f;
	for (i = 0; i < (int)(bsz / 4); i++)
		hB[i] = 0;
	for (k = 0; k < n; k++)
		for (c = 0; c < n; c++)
			hB[k * ldb + c] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, asz, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, bsz, hB, 0, NULL, NULL);
	SetKernelArg(kgemm_ldab, 0, sizeof(int), &n);
	SetKernelArg(kgemm_ldab, 1, sizeof(int), &rows);
	SetKernelArg(kgemm_ldab, 2, sizeof(int), &lda);
	SetKernelArg(kgemm_ldab, 3, sizeof(int), &ldb);
	SetKernelArg(kgemm_ldab, 4, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm_ldab, 5, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm_ldab, 6, sizeof(cl_mem), &bC);
	g[0] = (size_t)rows;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm_ldab, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm_ldab, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, csz, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK GEMMLDAB n=%d lda=%d ldb=%d gpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, lda, ldb, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[total - 1], (float)n);
	}
	return 0;
}

static int do_gemm_b(int n, int row0, int rows, int repeats, char *out, size_t outn)
{
	int i, total, it;
	size_t g[2], l[2];
	double t0, gpu;
	cl_int err;
	if (n % 16 != 0 || repeats < 1)
		return -1;
	if (ensure_gemm(n, rows) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm_b, 0, sizeof(int), &n);
	SetKernelArg(kgemm_b, 1, sizeof(int), &rows);
	SetKernelArg(kgemm_b, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm_b, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm_b, 4, sizeof(cl_mem), &bC);
	l[0] = 16;
	l[1] = 16;
	g[0] = ((size_t)rows + 15) / 16 * 16;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm_b, 2, NULL, g, l, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	for (it = 0; it < repeats; it++)
		EnqueueNDRange(q, kgemm_b, 2, NULL, g, l, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)total * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n * repeats;
		int mid = (total / 2);
		snprintf(out, outn,
			 "OK GEMMB n=%d row0=%d rows=%d reps=%d gpu_s=%.4f gflops=%.2f c0=%.1f cmid=%.1f clast=%.1f expect=%.1f\n",
			 n, row0, rows, repeats, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[mid], hC[total - 1], (float)n);
	}
	(void)err;
	gemm_row0 = row0;
	return 0;
}

static int do_gemm_f4(int n, int row0, int rows, int repeats, char *out, size_t outn)
{
	int i, total, it;
	size_t g[2];
	double t0, gpu;
	if (n % 4 != 0 || repeats < 1)
		return -1;
	if (ensure_gemm(n, rows) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm_f4, 0, sizeof(int), &n);
	SetKernelArg(kgemm_f4, 1, sizeof(int), &rows);
	SetKernelArg(kgemm_f4, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm_f4, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm_f4, 4, sizeof(cl_mem), &bC);
	g[0] = (size_t)rows;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm_f4, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	for (it = 0; it < repeats; it++)
		EnqueueNDRange(q, kgemm_f4, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)total * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)rows * n * n * repeats;
		snprintf(out, outn,
			 "OK GEMMF n=%d row0=%d rows=%d reps=%d gpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, row0, rows, repeats, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hC[0], hC[total - 1], (float)n);
	}
	gemm_row0 = row0;
	return 0;
}

static int do_gemm2(int n, int row0, int rows, char *out, size_t outn)
{
	int i, total;
	size_t g[2];
	double t0, gpu;
	if (ensure_gemm(n, rows) != 0)
		return -1;
	total = rows * n;
	for (i = 0; i < total; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm, 0, sizeof(int), &n);
	SetKernelArg(kgemm, 1, sizeof(int), &rows);
	SetKernelArg(kgemm, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm, 4, sizeof(cl_mem), &bC);
	g[0] = (size_t)rows;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	/* D = C * B into bA (overwrite A). C is rows x n, B is n x n. */
	SetKernelArg(kgemm, 2, sizeof(cl_mem), &bC);
	SetKernelArg(kgemm, 4, sizeof(cl_mem), &bA);
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	gpu = now_s() - t0;
	EnqueueRead(q, bA, 1, 0, (size_t)total * 4, hA, 0, NULL, NULL);
	{
		double flops = 2.0 * 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK GEMM2 n=%d row0=%d rows=%d gpu_s=%.4f gflops=%.2f d0=%.1f dlast=%.1f expect=%.1f\n",
			 n, row0, rows, gpu, gpu > 0 ? flops / gpu / 1e9 : 0,
			 hA[0], hA[total - 1], (float)n * (float)n);
	}
	return 0;
}

static void cpu_gemm(int n, int rows, const float *A, const float *B, float *C)
{
	int r;
	const int BS = 32;
#pragma omp parallel for
	for (r = 0; r < rows; r++) {
		int c, k, kk, cc;
		for (c = 0; c < n; c++)
			C[r * n + c] = 0;
		for (kk = 0; kk < n; kk += BS)
			for (cc = 0; cc < n; cc += BS)
				for (k = kk; k < kk + BS && k < n; k++) {
					float a = A[r * n + k];
					for (c = cc; c < cc + BS && c < n; c++)
						C[r * n + c] += a * B[k * n + c];
				}
	}
}

static int do_cpugemm(int n, int rows, char *out, size_t outn)
{
	int i, total;
	double t0, cpu;
	float *A, *B, *C;
	if (n < 8 || rows < 1 || n * rows > MAX_N)
		return -1;
	total = rows * n;
	A = malloc((size_t)total * 4);
	B = malloc((size_t)n * n * 4);
	C = malloc((size_t)total * 4);
	if (!A || !B || !C) {
		free(A);
		free(B);
		free(C);
		return -1;
	}
	for (i = 0; i < total; i++)
		A[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		B[i] = 1.0f;
	t0 = now_s();
	cpu_gemm(n, rows, A, B, C);
	cpu = now_s() - t0;
	{
		double flops = 2.0 * (double)rows * n * n;
		snprintf(out, outn,
			 "OK CPUGEMM n=%d rows=%d threads=%d cpu_s=%.4f gflops=%.2f c0=%.1f clast=%.1f expect=%.1f\n",
			 n, rows, omp_get_max_threads(), cpu,
			 cpu > 0 ? flops / cpu / 1e9 : 0, C[0], C[total - 1], (float)n);
	}
	free(A);
	free(B);
	free(C);
	return 0;
}

static int do_hetero(int n, int gpu_rows, int cpu_rows, char *out, size_t outn)
{
	int i, gtot, ctot;
	size_t g[2];
	double t0, wall, gt, ct;
	float *cA, *cC;
	if (gpu_rows < 1 || cpu_rows < 1)
		return -1;
	if (ensure_gemm(n, gpu_rows) != 0)
		return -1;
	gtot = gpu_rows * n;
	ctot = cpu_rows * n;
	cA = malloc((size_t)ctot * 4);
	cC = malloc((size_t)ctot * 4);
	if (!cA || !cC) {
		free(cA);
		free(cC);
		return -1;
	}
	for (i = 0; i < gtot; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	for (i = 0; i < ctot; i++)
		cA[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)gtot * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm, 0, sizeof(int), &n);
	SetKernelArg(kgemm, 1, sizeof(int), &gpu_rows);
	SetKernelArg(kgemm, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm, 4, sizeof(cl_mem), &bC);
	g[0] = (size_t)gpu_rows;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	gt = 0;
	ct = 0;
	{
		double tcpu = now_s();
		cpu_gemm(n, cpu_rows, cA, hB, cC);
		ct = now_s() - tcpu;
	}
	Finish(q);
	wall = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)gtot * 4, hC, 0, NULL, NULL);
	gt = wall - ct;
	if (gt < 0)
		gt = 0;
	{
		double flops = 2.0 * (double)(gpu_rows + cpu_rows) * n * n;
		snprintf(out, outn,
			 "OK HETERO n=%d gpu_rows=%d cpu_rows=%d wall=%.4f cpu_s=%.4f gflops=%.2f gc0=%.1f cc0=%.1f expect=%.1f\n",
			 n, gpu_rows, cpu_rows, wall, ct,
			 wall > 0 ? flops / wall / 1e9 : 0,
			 hC[0], cC[0], (float)n);
	}
	(void)gt;
	free(cA);
	free(cC);
	return 0;
}

static int do_hadd(int n, int gpu_rows, int cpu_rows, char *out, size_t outn)
{
	/* GPU does gpu_rows; CPU adds cpu_rows of independent extra GEMM. */
	int i, gtot, ctot;
	size_t g[2];
	double t0, wall, ct;
	float *cA, *cC;
	if (gpu_rows < 1 || cpu_rows < 1)
		return -1;
	if (ensure_gemm(n, gpu_rows) != 0)
		return -1;
	gtot = gpu_rows * n;
	ctot = cpu_rows * n;
	cA = malloc((size_t)ctot * 4);
	cC = malloc((size_t)ctot * 4);
	if (!cA || !cC) {
		free(cA);
		free(cC);
		return -1;
	}
	for (i = 0; i < gtot; i++)
		hA[i] = 1.0f;
	for (i = 0; i < n * n; i++)
		hB[i] = 1.0f;
	for (i = 0; i < ctot; i++)
		cA[i] = 1.0f;
	EnqueueWrite(q, bA, 1, 0, (size_t)gtot * 4, hA, 0, NULL, NULL);
	EnqueueWrite(q, bB, 1, 0, (size_t)n * n * 4, hB, 0, NULL, NULL);
	SetKernelArg(kgemm, 0, sizeof(int), &n);
	SetKernelArg(kgemm, 1, sizeof(int), &gpu_rows);
	SetKernelArg(kgemm, 2, sizeof(cl_mem), &bA);
	SetKernelArg(kgemm, 3, sizeof(cl_mem), &bB);
	SetKernelArg(kgemm, 4, sizeof(cl_mem), &bC);
	g[0] = (size_t)gpu_rows;
	g[1] = (size_t)n;
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	Finish(q);
	t0 = now_s();
	EnqueueNDRange(q, kgemm, 2, NULL, g, NULL, 0, NULL, NULL);
	{
		double tcpu = now_s();
		cpu_gemm(n, cpu_rows, cA, hB, cC);
		ct = now_s() - tcpu;
	}
	Finish(q);
	wall = now_s() - t0;
	EnqueueRead(q, bC, 1, 0, (size_t)gtot * 4, hC, 0, NULL, NULL);
	{
		double flops = 2.0 * (double)(gpu_rows + cpu_rows) * n * n;
		snprintf(out, outn,
			 "OK HADD n=%d gpu_rows=%d cpu_rows=%d wall=%.4f cpu_s=%.4f gflops=%.2f gc0=%.1f cc0=%.1f expect=%.1f\n",
			 n, gpu_rows, cpu_rows, wall, ct,
			 wall > 0 ? flops / wall / 1e9 : 0,
			 hC[0], cC[0], (float)n);
	}
	free(cA);
	free(cC);
	return 0;
}

static int setup_cl(void)
{
	void *h = dlopen("libOpenCL.so.1", RTLD_NOW);
	cl_int err = 0;
	cl_uint np = 0, nd = 0;
	cl_platform_id plat;
	cl_device_id dev;
	cl_program prog;
	const char *ps;

	if (!h)
		h = dlopen("libOpenCL.so", RTLD_NOW);
	if (!h)
		return -1;
	GetPlatformIDs = dlsym(h, "clGetPlatformIDs");
	GetDeviceIDs = dlsym(h, "clGetDeviceIDs");
	GetDeviceInfo = dlsym(h, "clGetDeviceInfo");
	CreateContext = dlsym(h, "clCreateContext");
	CreateCommandQueue = dlsym(h, "clCreateCommandQueue");
	CreateProgramWithSource = dlsym(h, "clCreateProgramWithSource");
	BuildProgram = dlsym(h, "clBuildProgram");
	CreateKernel = dlsym(h, "clCreateKernel");
	CreateBuffer = dlsym(h, "clCreateBuffer");
	ReleaseMem = dlsym(h, "clReleaseMemObject");
	SetKernelArg = dlsym(h, "clSetKernelArg");
	EnqueueNDRange = dlsym(h, "clEnqueueNDRangeKernel");
	EnqueueWrite = dlsym(h, "clEnqueueWriteBuffer");
	EnqueueRead = dlsym(h, "clEnqueueReadBuffer");
	Finish = dlsym(h, "clFinish");
	if (!GetPlatformIDs)
		return -1;
	GetPlatformIDs(1, &plat, &np);
	GetDeviceIDs(plat, CL_DEVICE_TYPE_GPU, 1, &dev, &nd);
	if (!nd)
		return -1;
	GetDeviceInfo(dev, 0x102B, sizeof(devname), devname, NULL);
	gdev = dev;
	ctx = CreateContext(NULL, 1, &dev, NULL, NULL, &err);
	q = CreateCommandQueue(ctx, dev, 0, &err);
	ps = ksrc;
	prog = CreateProgramWithSource(ctx, 1, &ps, NULL, &err);
	if (BuildProgram(prog, 1, &dev, NULL, NULL, NULL) != CL_SUCCESS) {
		cl_int (*GetProgramBuildInfo)(cl_program, cl_device_id, cl_uint, size_t, void *, size_t *) =
			dlsym(h, "clGetProgramBuildInfo");
		char blog[4096];
		blog[0] = 0;
		if (GetProgramBuildInfo)
			GetProgramBuildInfo(prog, dev, 0x1183, sizeof(blog), blog, NULL);
		fprintf(stderr, "OpenCL build failed:\n%s\n", blog);
		return -1;
	}
	k = CreateKernel(prog, "saxpy", &err);
	kgemm = CreateKernel(prog, "gemm_rows", &err);
	kgemm_b = CreateKernel(prog, "gemm_blocked", &err);
	kgemm_f4 = CreateKernel(prog, "gemm_f4", &err);
	kgemm_lda = CreateKernel(prog, "gemm_lda", &err);
	kgemm_ldab = CreateKernel(prog, "gemm_ldab", &err);
	kgemm_k16 = CreateKernel(prog, "gemm_k16", &err);
	{
		cl_int ferr = 0;
		cl_program pf = CreateProgramWithSource(ctx, 1, &ksrc_fast, NULL, &ferr);
		if (pf && ferr == CL_SUCCESS &&
		    BuildProgram(pf, 1, &gdev, "", NULL, NULL) == CL_SUCCESS)
			kgemm_fast = CreateKernel(pf, "gemm_fast", &ferr);
		if (ferr != CL_SUCCESS)
			kgemm_fast = NULL;
	}
	fprintf(stderr, "cl_daemon ready k16=%p\n", kgemm_k16);
	return 0;
}

static void handle(int fd)
{
	FILE *f = fdopen(fd, "r+");
	char line[256], out[512];
	if (!f)
		return;
	setvbuf(f, NULL, _IOLBF, 0);
	fprintf(f, "HELLO %s\n", devname);
	while (fgets(line, sizeof(line), f)) {
		int n, iters, base, secs;
		if (strncmp(line, "QUIT", 4) == 0) {
			fprintf(f, "BYE\n");
			break;
		}
		if (strncmp(line, "PING", 4) == 0) {
			fprintf(f, "PONG\n");
			continue;
		}
		if (strncmp(line, "INFO", 4) == 0) {
			if (do_info(out, sizeof(out)) == 0)
				fputs(out, f);
			else
				fprintf(f, "ERR info\n");
			continue;
		}
		if (sscanf(line, "CPUGEMM %d %d", &n, &base) == 2) {
			if (do_cpugemm(n, base, out, sizeof(out)) == 0)
				fputs(out, f);
			else
				fprintf(f, "ERR cpugemm\n");
			continue;
		}
		{
			int gr, cr;
			if (sscanf(line, "HETERO %d %d %d", &n, &gr, &cr) == 3) {
				if (do_hetero(n, gr, cr, out, sizeof(out)) == 0)
					fputs(out, f);
				else
					fprintf(f, "ERR hetero\n");
				continue;
			}
			if (sscanf(line, "HADD %d %d %d", &n, &gr, &cr) == 3) {
				if (do_hadd(n, gr, cr, out, sizeof(out)) == 0)
					fputs(out, f);
				else
					fprintf(f, "ERR hadd\n");
				continue;
			}
		}
		if (sscanf(line, "PREP %d %d", &n, &base) == 2) {
			if (do_prep(n, base, out, sizeof(out)) == 0)
				fputs(out, f);
			else
				fprintf(f, "ERR prep\n");
			continue;
		}
		if (sscanf(line, "KERNEL_NR %d %d", &n, &iters) == 2) {
			if (do_kernel_only(n, iters, 0, out, sizeof(out)) == 0)
				fputs(out, f);
			else
				fprintf(f, "ERR kernel_nr\n");
			continue;
		}
		if (sscanf(line, "KERNEL %d %d", &n, &iters) == 2) {
			if (do_kernel_only(n, iters, 1, out, sizeof(out)) == 0)
				fputs(out, f);
			else
				fprintf(f, "ERR kernel\n");
			continue;
		}
		{
			int rows, row0;
			{
				int lda, ldb;
				if (sscanf(line, "GEMMLDAB %d %d %d %d %d", &n, &row0, &rows, &lda, &ldb) == 5) {
					if (do_gemm_ldab(n, row0, rows, lda, ldb, out, sizeof(out)) == 0)
						fputs(out, f);
					else
						fprintf(f, "ERR gemmldab\n");
					continue;
				}
			}
			{
				int lda;
				if (sscanf(line, "GEMMLDA %d %d %d %d", &n, &row0, &rows, &lda) == 4) {
					if (do_gemm_lda(n, row0, rows, lda, out, sizeof(out)) == 0)
						fputs(out, f);
					else
						fprintf(f, "ERR gemmlda\n");
					continue;
				}
			}
			{
				int g1;
				if (sscanf(line, "GEMMG %d %d %d %d", &n, &row0, &rows, &g1) == 4) {
					if (do_gemmg(n, row0, rows, g1, out, sizeof(out)) == 0)
						fputs(out, f);
					else
						fprintf(f, "ERR gemmg\n");
					continue;
				}
			}
			if (sscanf(line, "GEMMT %d %d %d", &n, &row0, &rows) == 3) {
				if (do_gemm_t(n, row0, rows, out, sizeof(out)) == 0)
					fputs(out, f);
				else
					fprintf(f, "ERR gemmt\n");
				continue;
			}
			if (sscanf(line, "GEMMK16 %d %d %d", &n, &row0, &rows) == 3) {
				if (do_gemm_k16(n, row0, rows, out, sizeof(out)) == 0)
					fputs(out, f);
				else
					fprintf(f, "ERR gemmk16\n");
				continue;
			}
			if (sscanf(line, "GEMM2 %d %d %d", &n, &row0, &rows) == 3) {
				if (do_gemm2(n, row0, rows, out, sizeof(out)) == 0)
					fputs(out, f);
				else
					fprintf(f, "ERR gemm2\n");
				continue;
			}
			if (sscanf(line, "GEMM %d %d %d", &n, &row0, &rows) == 3) {
				if (do_gemm(n, row0, rows, out, sizeof(out)) == 0)
					fputs(out, f);
				else
					fprintf(f, "ERR gemm\n");
				continue;
			}
			{
				int reps;
				if (sscanf(line, "GEMMB %d %d %d %d", &n, &row0, &rows, &reps) == 4) {
					if (do_gemm_b(n, row0, rows, reps, out, sizeof(out)) == 0)
						fputs(out, f);
					else
						fprintf(f, "ERR gemmb\n");
					continue;
				}
				if (sscanf(line, "GEMMF %d %d %d %d", &n, &row0, &rows, &reps) == 4) {
					if (do_gemm_f4(n, row0, rows, reps, out, sizeof(out)) == 0)
						fputs(out, f);
					else
						fprintf(f, "ERR gemmf\n");
					continue;
				}
			}
		}
		if (sscanf(line, "SAXPY %d %d %d", &n, &iters, &base) == 3) {
			if (do_saxpy(n, iters, base, 1, out, sizeof(out)) == 0)
				fputs(out, f);
			else
				fprintf(f, "ERR saxpy\n");
			continue;
		}
		if (sscanf(line, "BURN %d %d %d %d", &n, &iters, &base, &secs) == 4) {
			double t0 = now_s();
			int loops = 1;
			if (secs > 60)
				secs = 60;
			if (secs < 1)
				secs = 1;
			/* 8M SAXPY ~1.75ms/NDRange; iters=20 → ~35ms/loop. loops≈secs/0.035 */
			loops = secs * 28;
			if (loops < 1)
				loops = 1;
			if (do_saxpy(n, iters, base, loops, out, sizeof(out)) == 0) {
				fprintf(f, "BURN wall=%.3f %s", now_s() - t0, out);
			} else {
				fprintf(f, "ERR burn\n");
			}
			continue;
		}
		fprintf(f, "ERR unknown\n");
	}
	fclose(f);
}

int main(void)
{
	int s, c;
	struct sockaddr_in addr;
	int opt = 1;

	if (setup_cl() != 0) {
		fprintf(stderr, "OpenCL setup failed\n");
		return 1;
	}
	s = socket(AF_INET, SOCK_STREAM, 0);
	setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = htonl(INADDR_ANY);
	addr.sin_port = htons(PORT);
	if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
		perror("bind");
		return 1;
	}
	listen(s, 4);
	fprintf(stderr, "listening on %d\n", PORT);
	for (;;) {
		c = accept(s, NULL, NULL);
		if (c < 0)
			continue;
		handle(c);
	}
}
