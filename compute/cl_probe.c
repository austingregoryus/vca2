// SPDX-License-Identifier: MIT
/* Minimal OpenCL probe for VCA P580. No CL/cl.h required. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int cl_int;
typedef unsigned int cl_uint;
typedef unsigned long cl_ulong;
typedef cl_ulong cl_bitfield;
typedef cl_bitfield cl_device_type;
typedef cl_uint cl_platform_info;
typedef cl_uint cl_device_info;
typedef void *cl_platform_id;
typedef void *cl_device_id;
typedef void *cl_context;
typedef void *cl_command_queue;
typedef void *cl_program;
typedef void *cl_kernel;
typedef void *cl_mem;

#define CL_SUCCESS 0
#define CL_DEVICE_TYPE_GPU (1 << 2)
#define CL_DEVICE_TYPE_CPU (1 << 1)
#define CL_DEVICE_TYPE_ALL 0xFFFFFFFF
#define CL_PLATFORM_NAME 0x0902
#define CL_DEVICE_NAME 0x102B
#define CL_DEVICE_VENDOR 0x102C
#define CL_DEVICE_VERSION 0x102F
#define CL_DEVICE_MAX_COMPUTE_UNITS 0x1002 /* was 0x1021 = MAX_CONSTANT_ARGS, which is why this printed 8 */
#define CL_DEVICE_GLOBAL_MEM_SIZE 0x101F
#define CL_DEVICE_TYPE 0x1000
#define CL_MEM_READ_WRITE (1 << 0)
#define CL_MEM_COPY_HOST_PTR (1 << 5)

typedef cl_int (*pGetPlatformIDs)(cl_uint, cl_platform_id *, cl_uint *);
typedef cl_int (*pGetPlatformInfo)(cl_platform_id, cl_platform_info, size_t, void *, size_t *);
typedef cl_int (*pGetDeviceIDs)(cl_platform_id, cl_device_type, cl_uint, cl_device_id *, cl_uint *);
typedef cl_int (*pGetDeviceInfo)(cl_device_id, cl_device_info, size_t, void *, size_t *);
typedef cl_context (*pCreateContext)(void *, cl_uint, const cl_device_id *, void *, void *, cl_int *);
typedef cl_int (*pReleaseContext)(cl_context);
typedef cl_command_queue (*pCreateCommandQueue)(cl_context, cl_device_id, cl_ulong, cl_int *);
typedef cl_program (*pCreateProgramWithSource)(cl_context, cl_uint, const char **, const size_t *, cl_int *);
typedef cl_int (*pBuildProgram)(cl_program, cl_uint, const cl_device_id *, const char *, void *, void *);
typedef cl_int (*pGetProgramBuildInfo)(cl_program, cl_device_id, cl_uint, size_t, void *, size_t *);
typedef cl_kernel (*pCreateKernel)(cl_program, const char *, cl_int *);
typedef cl_mem (*pCreateBuffer)(cl_context, cl_bitfield, size_t, void *, cl_int *);
typedef cl_int (*pSetKernelArg)(cl_kernel, cl_uint, size_t, const void *);
typedef cl_int (*pEnqueueNDRange)(cl_command_queue, cl_kernel, cl_uint, const size_t *, const size_t *, const size_t *, cl_uint, const void *, void *);
typedef cl_int (*pEnqueueRead)(cl_command_queue, cl_mem, cl_uint, size_t, size_t, void *, cl_uint, const void *, void *);
typedef cl_int (*pFinish)(cl_command_queue);

#define CL_PROGRAM_BUILD_LOG 0x1183

static const char *src =
	"__kernel void saxpy(int n, float a, __global const float *x, __global float *y) {\n"
	"  int i = get_global_id(0);\n"
	"  if (i < n) y[i] = a * x[i] + y[i];\n"
	"}\n";

static void *must(void *h, const char *n)
{
	void *p = dlsym(h, n);
	if (!p)
		fprintf(stderr, "missing %s: %s\n", n, dlerror());
	return p;
}

int main(void)
{
	const char *libs[] = {
		"libOpenCL.so.1", "libOpenCL.so",
		"/usr/local/lib64/libigdrcl.so", NULL
	};
	void *h = NULL;
	int i;
	for (i = 0; libs[i]; i++) {
		h = dlopen(libs[i], RTLD_NOW);
		if (h) {
			printf("loaded %s\n", libs[i]);
			break;
		}
	}
	if (!h) {
		fprintf(stderr, "no OpenCL ICD/runtime: %s\n", dlerror());
		return 1;
	}

	pGetPlatformIDs GetPlatformIDs = must(h, "clGetPlatformIDs");
	pGetPlatformInfo GetPlatformInfo = must(h, "clGetPlatformInfo");
	pGetDeviceIDs GetDeviceIDs = must(h, "clGetDeviceIDs");
	pGetDeviceInfo GetDeviceInfo = must(h, "clGetDeviceInfo");
	pCreateContext CreateContext = must(h, "clCreateContext");
	pCreateCommandQueue CreateCommandQueue = must(h, "clCreateCommandQueue");
	pCreateProgramWithSource CreateProgramWithSource = must(h, "clCreateProgramWithSource");
	pBuildProgram BuildProgram = must(h, "clBuildProgram");
	pGetProgramBuildInfo GetProgramBuildInfo = must(h, "clGetProgramBuildInfo");
	pCreateKernel CreateKernel = must(h, "clCreateKernel");
	pCreateBuffer CreateBuffer = must(h, "clCreateBuffer");
	pSetKernelArg SetKernelArg = must(h, "clSetKernelArg");
	pEnqueueNDRange EnqueueNDRange = must(h, "clEnqueueNDRangeKernel");
	pEnqueueRead EnqueueRead = must(h, "clEnqueueReadBuffer");
	pFinish Finish = must(h, "clFinish");
	if (!GetPlatformIDs)
		return 1;

	cl_uint np = 0;
	cl_int err = GetPlatformIDs(0, NULL, &np);
	printf("platforms=%u err=%d\n", np, err);
	if (!np)
		return 2;
	cl_platform_id plats[8];
	GetPlatformIDs(np > 8 ? 8 : np, plats, NULL);

	cl_device_id dev = NULL;
	for (i = 0; i < (int)np && i < 8; i++) {
		char name[128] = {0};
		GetPlatformInfo(plats[i], CL_PLATFORM_NAME, sizeof(name), name, NULL);
		cl_uint nd = 0;
		GetDeviceIDs(plats[i], CL_DEVICE_TYPE_ALL, 0, NULL, &nd);
		printf("platform[%d] %s devices=%u\n", i, name, nd);
		if (!nd)
			continue;
		cl_device_id ds[8];
		GetDeviceIDs(plats[i], CL_DEVICE_TYPE_ALL, nd > 8 ? 8 : nd, ds, NULL);
		cl_uint j;
		for (j = 0; j < nd && j < 8; j++) {
			char dn[128] = {0}, dv[64] = {0};
			cl_uint cu = 0;
			cl_ulong mem = 0;
			cl_device_type t = 0;
			GetDeviceInfo(ds[j], CL_DEVICE_NAME, sizeof(dn), dn, NULL);
			GetDeviceInfo(ds[j], CL_DEVICE_VERSION, sizeof(dv), dv, NULL);
			GetDeviceInfo(ds[j], CL_DEVICE_MAX_COMPUTE_UNITS, sizeof(cu), &cu, NULL);
			GetDeviceInfo(ds[j], CL_DEVICE_GLOBAL_MEM_SIZE, sizeof(mem), &mem, NULL);
			GetDeviceInfo(ds[j], CL_DEVICE_TYPE, sizeof(t), &t, NULL);
			printf("  dev[%u] type=0x%lx %s %s CUs=%u glob=%lu\n",
			       j, (unsigned long)t, dn, dv, cu, (unsigned long)mem);
			if (!dev && (t & CL_DEVICE_TYPE_GPU))
				dev = ds[j];
			if (!dev)
				dev = ds[j];
		}
	}
	if (!dev) {
		fprintf(stderr, "no device\n");
		return 3;
	}

	cl_context ctx = CreateContext(NULL, 1, &dev, NULL, NULL, &err);
	printf("context err=%d\n", err);
	cl_command_queue q = CreateCommandQueue(ctx, dev, 0, &err);
	printf("queue err=%d\n", err);
	const char *psrc = src;
	cl_program prog = CreateProgramWithSource(ctx, 1, &psrc, NULL, &err);
	err = BuildProgram(prog, 1, &dev, NULL, NULL, NULL);
	if (err != CL_SUCCESS) {
		char log[2048] = {0};
		GetProgramBuildInfo(prog, dev, CL_PROGRAM_BUILD_LOG, sizeof(log), log, NULL);
		fprintf(stderr, "build err=%d\n%s\n", err, log);
		return 4;
	}
	cl_kernel k = CreateKernel(prog, "saxpy", &err);
	printf("kernel err=%d\n", err);

	enum { N = 1024 };
	float x[N], y[N], a = 2.0f;
	int n = N;
	for (i = 0; i < N; i++) {
		x[i] = (float)i;
		y[i] = 1.0f;
	}
	cl_mem bx = CreateBuffer(ctx, CL_MEM_READ_WRITE | CL_MEM_COPY_HOST_PTR, sizeof(x), x, &err);
	cl_mem by = CreateBuffer(ctx, CL_MEM_READ_WRITE | CL_MEM_COPY_HOST_PTR, sizeof(y), y, &err);
	SetKernelArg(k, 0, sizeof(int), &n);
	SetKernelArg(k, 1, sizeof(float), &a);
	SetKernelArg(k, 2, sizeof(cl_mem), &bx);
	SetKernelArg(k, 3, sizeof(cl_mem), &by);
	size_t g = N;
	err = EnqueueNDRange(q, k, 1, NULL, &g, NULL, 0, NULL, NULL);
	printf("ndrange err=%d\n", err);
	err = EnqueueRead(q, by, 1, 0, sizeof(y), y, 0, NULL, NULL);
	Finish(q);
	printf("read err=%d y[0]=%.1f y[10]=%.1f expect 1 and 21\n", err, y[0], y[10]);
	if (y[0] != 1.0f || y[10] != 21.0f) {
		fprintf(stderr, "SAXPY mismatch\n");
		return 5;
	}
	printf("SAXPY OK on GPU/CPU OpenCL device\n");
	return 0;
}
