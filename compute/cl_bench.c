// SPDX-License-Identifier: MIT
/* Time SAXPY on the P580 vs a CPU loop. Same binary as cl_probe, larger N. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef int cl_int;
typedef unsigned int cl_uint;
typedef unsigned long cl_ulong;
typedef cl_ulong cl_bitfield;
typedef cl_bitfield cl_device_type;
typedef void *cl_platform_id;
typedef void *cl_device_id;
typedef void *cl_context;
typedef void *cl_command_queue;
typedef void *cl_program;
typedef void *cl_kernel;
typedef void *cl_mem;

#define CL_SUCCESS 0
#define CL_DEVICE_TYPE_GPU (1u << 2)
#define CL_DEVICE_TYPE_ALL 0xFFFFFFFFu
#define CL_DEVICE_NAME 0x102B
#define CL_MEM_READ_WRITE (1u << 0)
#define CL_MEM_COPY_HOST_PTR (1u << 5)
#define CL_MEM_READ_ONLY (1u << 2)

typedef cl_int (*pGetPlatformIDs)(cl_uint, cl_platform_id *, cl_uint *);
typedef cl_int (*pGetDeviceIDs)(cl_platform_id, cl_device_type, cl_uint, cl_device_id *, cl_uint *);
typedef cl_int (*pGetDeviceInfo)(cl_device_id, cl_uint, size_t, void *, size_t *);
typedef cl_context (*pCreateContext)(void *, cl_uint, const cl_device_id *, void *, void *, cl_int *);
typedef cl_command_queue (*pCreateCommandQueue)(cl_context, cl_device_id, cl_ulong, cl_int *);
typedef cl_program (*pCreateProgramWithSource)(cl_context, cl_uint, const char **, const size_t *, cl_int *);
typedef cl_int (*pBuildProgram)(cl_program, cl_uint, const cl_device_id *, const char *, void *, void *);
typedef cl_kernel (*pCreateKernel)(cl_program, const char *, cl_int *);
typedef cl_mem (*pCreateBuffer)(cl_context, cl_bitfield, size_t, void *, cl_int *);
typedef cl_int (*pSetKernelArg)(cl_kernel, cl_uint, size_t, const void *);
typedef cl_int (*pEnqueueNDRange)(cl_command_queue, cl_kernel, cl_uint, const size_t *, const size_t *, const size_t *, cl_uint, const void *, void *);
typedef cl_int (*pEnqueueWrite)(cl_command_queue, cl_mem, cl_uint, size_t, size_t, const void *, cl_uint, const void *, void *);
typedef cl_int (*pEnqueueRead)(cl_command_queue, cl_mem, cl_uint, size_t, size_t, void *, cl_uint, const void *, void *);
typedef cl_int (*pFinish)(cl_command_queue);

static double now_s(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static const char *src =
	"__kernel void saxpy(int n, float a, __global const float *x, __global float *y) {\n"
	"  int i = get_global_id(0);\n"
	"  if (i < n) y[i] = a * x[i] + y[i];\n"
	"}\n";

int main(int argc, char **argv)
{
	int N = 8 * 1024 * 1024;
	int iters = 20;
	int gpu_only = 0;
	int base = 0;
	if (argc > 1)
		N = atoi(argv[1]);
	if (argc > 2)
		iters = atoi(argv[2]);
	if (argc > 3)
		gpu_only = atoi(argv[3]);
	if (argc > 4)
		base = atoi(argv[4]);

	void *h = dlopen("libOpenCL.so.1", RTLD_NOW);
	if (!h)
		h = dlopen("libOpenCL.so", RTLD_NOW);
	if (!h) {
		fprintf(stderr, "dlopen OpenCL: %s\n", dlerror());
		return 1;
	}
	pGetPlatformIDs GetPlatformIDs = dlsym(h, "clGetPlatformIDs");
	pGetDeviceIDs GetDeviceIDs = dlsym(h, "clGetDeviceIDs");
	pGetDeviceInfo GetDeviceInfo = dlsym(h, "clGetDeviceInfo");
	pCreateContext CreateContext = dlsym(h, "clCreateContext");
	pCreateCommandQueue CreateCommandQueue = dlsym(h, "clCreateCommandQueue");
	pCreateProgramWithSource CreateProgramWithSource = dlsym(h, "clCreateProgramWithSource");
	pBuildProgram BuildProgram = dlsym(h, "clBuildProgram");
	pCreateKernel CreateKernel = dlsym(h, "clCreateKernel");
	pCreateBuffer CreateBuffer = dlsym(h, "clCreateBuffer");
	pSetKernelArg SetKernelArg = dlsym(h, "clSetKernelArg");
	pEnqueueNDRange EnqueueNDRange = dlsym(h, "clEnqueueNDRangeKernel");
	pEnqueueWrite EnqueueWrite = dlsym(h, "clEnqueueWriteBuffer");
	pEnqueueRead EnqueueRead = dlsym(h, "clEnqueueReadBuffer");
	pFinish Finish = dlsym(h, "clFinish");

	cl_uint np = 0;
	GetPlatformIDs(0, NULL, &np);
	cl_platform_id plat;
	GetPlatformIDs(1, &plat, NULL);
	cl_uint nd = 0;
	GetDeviceIDs(plat, CL_DEVICE_TYPE_GPU, 0, NULL, &nd);
	if (!nd) {
		fprintf(stderr, "no GPU\n");
		return 2;
	}
	cl_device_id dev;
	GetDeviceIDs(plat, CL_DEVICE_TYPE_GPU, 1, &dev, NULL);
	char name[128] = {0};
	GetDeviceInfo(dev, CL_DEVICE_NAME, sizeof(name), name, NULL);

	float *x = malloc((size_t)N * sizeof(float));
	float *y = malloc((size_t)N * sizeof(float));
	float *yc = malloc((size_t)N * sizeof(float));
	if (!x || !y || !yc)
		return 3;
	int i;
	for (i = 0; i < N; i++) {
		x[i] = 0.001f * (float)(base + i);
		y[i] = 1.0f;
		yc[i] = 1.0f;
	}
	float a = 1.5f;

	double t0 = now_s();
	int it;
	double cpu = 0.0;
	if (!gpu_only) {
		for (it = 0; it < iters; it++) {
			for (i = 0; i < N; i++)
				yc[i] = a * x[i] + yc[i];
		}
		cpu = now_s() - t0;
	}

	cl_int err = 0;
	cl_context ctx = CreateContext(NULL, 1, &dev, NULL, NULL, &err);
	cl_command_queue q = CreateCommandQueue(ctx, dev, 0, &err);
	const char *ps = src;
	cl_program prog = CreateProgramWithSource(ctx, 1, &ps, NULL, &err);
	if (BuildProgram(prog, 1, &dev, NULL, NULL, NULL) != CL_SUCCESS) {
		fprintf(stderr, "build fail\n");
		return 4;
	}
	cl_kernel k = CreateKernel(prog, "saxpy", &err);
	cl_mem bx = CreateBuffer(ctx, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR, (size_t)N * 4, x, &err);
	cl_mem by = CreateBuffer(ctx, CL_MEM_READ_WRITE | CL_MEM_COPY_HOST_PTR, (size_t)N * 4, y, &err);
	SetKernelArg(k, 0, sizeof(int), &N);
	SetKernelArg(k, 1, sizeof(float), &a);
	SetKernelArg(k, 2, sizeof(cl_mem), &bx);
	SetKernelArg(k, 3, sizeof(cl_mem), &by);
	size_t g = (size_t)N;
	EnqueueNDRange(q, k, 1, NULL, &g, NULL, 0, NULL, NULL);
	Finish(q);

	t0 = now_s();
	for (it = 0; it < iters; it++)
		EnqueueNDRange(q, k, 1, NULL, &g, NULL, 0, NULL, NULL);
	Finish(q);
	double gpu = now_s() - t0;
	EnqueueRead(q, by, 1, 0, (size_t)N * 4, y, 0, NULL, NULL);

	/* CPU ran iters times on yc; GPU ran 1 warmup + iters on y starting from 1 */
	printf("device=%s N=%d iters=%d base=%d\n", name, N, iters, base);
	printf("cpu_s=%.4f gpu_kernel_s=%.4f speedup=%.2fx\n", cpu, gpu,
	       (cpu > 0 && gpu > 0) ? cpu / gpu : 0);
	double flops = 2.0 * (double)N * (double)iters;
	printf("cpu_gflops=%.2f gpu_gflops=%.2f\n",
	       cpu > 0 ? flops / cpu / 1e9 : 0,
	       gpu > 0 ? flops / gpu / 1e9 : 0);
	printf("check y[0]=%.4f y[100]=%.4f y[last]=%.4f\n", y[0], y[100], y[N - 1]);
	printf("TILE base=%d N=%d y0=%.6f y100=%.6f ylast=%.6f\n",
	       base, N, y[0], y[100], y[N - 1]);
	free(x);
	free(y);
	free(yc);
	return 0;
}
