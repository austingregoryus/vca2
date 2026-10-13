// SPDX-License-Identifier: MIT
/*
 * gpuburn SECONDS [n] [rows]
 *
 * Sustained P580 load using the tuned GEMM. Context and buffers are created
 * once and the kernel is re-enqueued continuously, so the CPU contributes
 * almost nothing -- unlike respawning gemm_fast, which spends most of its
 * time filling host buffers.
 *
 * Reports GFLOP/s per 10 s window, so sustained-vs-burst throttling shows up
 * directly.
 */
#define CL_USE_DEPRECATED_OPENCL_1_2_APIS
#include <CL/cl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static const char *src =
"#define R 8\n#define TK 32\n"
"__kernel void g(int n, int rows, __global const float *A,\n"
"                __global const float *B, __global float *C) {\n"
"  int c2 = get_global_id(0) * 2;\n"
"  int lc = get_local_id(0);\n"
"  int ls = get_local_size(0);\n"
"  int r0 = get_global_id(1) * R;\n"
"  __local float As[R][TK];\n"
"  float2 acc[R];\n"
"  for (int i = 0; i < R; i++) acc[i] = (float2)(0.0f,0.0f);\n"
"  for (int kb = 0; kb < n; kb += TK) {\n"
"    barrier(CLK_LOCAL_MEM_FENCE);\n"
"    for (int t = lc; t < R*TK; t += ls) {\n"
"      int i = t / TK, kk = t - i*TK;\n"
"      As[i][kk] = (r0+i < rows && kb+kk < n) ? A[(r0+i)*n + kb+kk] : 0.0f;\n"
"    }\n"
"    barrier(CLK_LOCAL_MEM_FENCE);\n"
"    if (c2 < n) {\n"
"      int kmax = (n - kb) < TK ? (n - kb) : TK;\n"
"      for (int kk = 0; kk < kmax; kk++) {\n"
"        float2 b = vload2(0, B + (kb+kk)*n + c2);\n"
"        for (int i = 0; i < R; i++) acc[i] += As[i][kk] * b;\n"
"      }\n"
"    }\n"
"  }\n"
"  if (c2 < n) for (int i = 0; i < R; i++) if (r0+i < rows) vstore2(acc[i],0,C+(r0+i)*n+c2);\n"
"}\n";

static double now_s(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
#define CHK(x,m) do{ if((x)!=CL_SUCCESS){fprintf(stderr,"FAIL %s err=%d\n",m,(int)(x));exit(1);} }while(0)

int main(int argc,char**argv){
    double secs = argc>1?atof(argv[1]):120.0;
    int n = argc>2?atoi(argv[2]):1408;
    int rows = argc>3?atoi(argv[3]):469;
    cl_platform_id p;cl_device_id dev;cl_int err;cl_context ctx;cl_command_queue q;cl_program prog;cl_kernel kk;
    cl_mem bA,bB,bC; float *hA,*hB;
    size_t asz=(size_t)rows*n*4, bsz=(size_t)n*n*4, g[2],l[2];
    double t0,tw,el; long iters=0,witers=0;

    hA=malloc(asz);hB=malloc(bsz);
    for(size_t i=0;i<asz/4;i++)hA[i]=1.0f;
    for(size_t i=0;i<bsz/4;i++)hB[i]=1.0f;
    CHK(clGetPlatformIDs(1,&p,NULL),"plat");
    CHK(clGetDeviceIDs(p,CL_DEVICE_TYPE_GPU,1,&dev,NULL),"dev");
    ctx=clCreateContext(NULL,1,&dev,NULL,NULL,&err);CHK(err,"ctx");
    q=clCreateCommandQueue(ctx,dev,0,&err);CHK(err,"q");
    prog=clCreateProgramWithSource(ctx,1,&src,NULL,&err);CHK(err,"prog");
    if(clBuildProgram(prog,1,&dev,"",NULL,NULL)!=CL_SUCCESS){
        char*log=malloc(65536);size_t ls;clGetProgramBuildInfo(prog,dev,CL_PROGRAM_BUILD_LOG,65536,log,&ls);
        fprintf(stderr,"%s\n",log);return 1;}
    kk=clCreateKernel(prog,"g",&err);CHK(err,"kern");
    bA=clCreateBuffer(ctx,CL_MEM_READ_ONLY,asz,NULL,&err);CHK(err,"bA");
    bB=clCreateBuffer(ctx,CL_MEM_READ_ONLY,bsz,NULL,&err);CHK(err,"bB");
    bC=clCreateBuffer(ctx,CL_MEM_READ_WRITE,asz,NULL,&err);CHK(err,"bC");
    CHK(clEnqueueWriteBuffer(q,bA,CL_TRUE,0,asz,hA,0,NULL,NULL),"wA");
    CHK(clEnqueueWriteBuffer(q,bB,CL_TRUE,0,bsz,hB,0,NULL,NULL),"wB");
    CHK(clSetKernelArg(kk,0,sizeof(int),&n),"a0");
    CHK(clSetKernelArg(kk,1,sizeof(int),&rows),"a1");
    CHK(clSetKernelArg(kk,2,sizeof(cl_mem),&bA),"a2");
    CHK(clSetKernelArg(kk,3,sizeof(cl_mem),&bB),"a3");
    CHK(clSetKernelArg(kk,4,sizeof(cl_mem),&bC),"a4");
    l[0]=256;l[1]=1;
    g[0]=((n+1)/2 + 255)/256*256; g[1]=(rows+7)/8;
    setvbuf(stdout,NULL,_IOLBF,0);
    t0=now_s(); tw=t0;
    for(;;){
        int b;
        for(b=0;b<8;b++){ CHK(clEnqueueNDRangeKernel(q,kk,2,NULL,g,l,0,NULL,NULL),"ndr"); }
        clFinish(q); iters+=8; witers+=8;
        el=now_s()-t0;
        if(now_s()-tw>=10.0){
            double w=now_s()-tw;
            printf("  t=%5.0fs  %7.1f GFLOP/s (window)\n", el,
                   2.0*rows*n*(double)n*witers/w/1e9);
            tw=now_s(); witers=0;
        }
        if(el>=secs) break;
    }
    el=now_s()-t0;
    printf("SUSTAINED %.1f GFLOP/s over %.0fs (%ld iters, n=%d rows=%d)\n",
           2.0*rows*n*(double)n*iters/el/1e9, el, iters, n, rows);
    return 0;
}
