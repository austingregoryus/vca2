// SPDX-License-Identifier: MIT
/*
 * gemm_fast [n] [rows] [variant]
 *
 * Tuned P580 GEMM: R rows per work-item, float2 columns, A staged in __local,
 * dim 0 = column (SIMD-coalesced), local = {256,1}.
 *
 * local size in dim 1 MUST stay 1: As[][] is per-workgroup but the row block
 * is r0 = get_global_id(1)*R, so a dim-1 local size > 1 races two row blocks
 * onto one tile. That bug is invisible to an A=1/B=1/C==n check -- it was
 * caught only by comparing against a CPU reference with random data.
 *
 * variant: 0 = naive (the old shipped kernel, for comparison)
 *          1 = r8 TK32   2 = r8 TK64   3 = r4 TK32   4 = r16 TK32
 * Always self-checks against a CPU reference when -v is passed.
 */
#define CL_USE_DEPRECATED_OPENCL_1_2_APIS
#include <CL/cl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

static const char *src =
"#define RBL2(NAME,R,TK) \\\n"
"__kernel void NAME(int n, int rows, __global const float *A,\\\n"
"                   __global const float *B, __global float *C) {\\\n"
"  int c2 = get_global_id(0) * 2;\\\n"
"  int lc = get_local_id(0);\\\n"
"  int ls = get_local_size(0);\\\n"
"  int r0 = get_global_id(1) * R;\\\n"
"  __local float As[R][TK];\\\n"
"  float2 acc[R];\\\n"
"  for (int i = 0; i < R; i++) acc[i] = (float2)(0.0f,0.0f);\\\n"
"  for (int kb = 0; kb < n; kb += TK) {\\\n"
"    barrier(CLK_LOCAL_MEM_FENCE);\\\n"
"    for (int t = lc; t < R*TK; t += ls) {\\\n"
"      int i = t / TK, kk = t - i*TK;\\\n"
"      As[i][kk] = (r0+i < rows && kb+kk < n) ? A[(r0+i)*n + kb+kk] : 0.0f;\\\n"
"    }\\\n"
"    barrier(CLK_LOCAL_MEM_FENCE);\\\n"
"    if (c2 < n) {\\\n"
"      int kmax = (n - kb) < TK ? (n - kb) : TK;\\\n"
"      for (int kk = 0; kk < kmax; kk++) {\\\n"
"        float2 b = vload2(0, B + (kb+kk)*n + c2);\\\n"
"        for (int i = 0; i < R; i++) acc[i] += As[i][kk] * b;\\\n"
"      }\\\n"
"    }\\\n"
"  }\\\n"
"  if (c2 < n) for (int i = 0; i < R; i++) if (r0+i < rows) vstore2(acc[i],0,C+(r0+i)*n+c2);\\\n"
"}\n"
"RBL2(g_r8_32,8,32)\n"
"RBL2(g_r8_64,8,64)\n"
"RBL2(g_r4_32,4,32)\n"
"RBL2(g_r16_32,16,32)\n"
"__kernel void g_naive(int n,int rows,__global const float*A,\n"
"                      __global const float*B,__global float*C){\n"
"  int r=get_global_id(0),c=get_global_id(1);\n"
"  if(r>=rows||c>=n)return; float s=0;\n"
"  for(int k=0;k<n;k++) s+=A[r*n+k]*B[k*n+c];\n"
"  C[r*n+c]=s;\n}\n";

static double now_s(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
#define CHK(x,m) do{ if((x)!=CL_SUCCESS){fprintf(stderr,"FAIL %s err=%d L%d\n",m,(int)(x),__LINE__);exit(1);} }while(0)

struct KV { const char *kern; int rdiv, wdiv, swap; size_t l0, l1; };
static struct KV kvs[] = {
  {"g_naive", 1,1,1, 1,128},
  {"g_r8_32", 8,2,0, 256,1},
  {"g_r8_64", 8,2,0, 256,1},
  {"g_r4_32", 4,2,0, 256,1},
  {"g_r16_32",16,2,0,256,1},
};

int main(int argc,char**argv){
    int n=argc>1?atoi(argv[1]):1536, rows=argc>2?atoi(argv[2]):512;
    int var=argc>3?atoi(argv[3]):1;
    int verify=(argc>4 && argv[4][0]=='v');
    cl_platform_id p;cl_device_id dev;cl_int err;cl_context ctx;cl_command_queue q;cl_program prog;
    cl_mem bA,bB,bC; float *hA,*hB,*hC,*ref=NULL;
    size_t asz=(size_t)rows*n*4, bsz=(size_t)n*n*4, g[2],l[2];
    unsigned seed=12345; int i,reps=3; double best=1e30,t0,dt;
    struct KV kv;

    if(var<0||var>=(int)(sizeof(kvs)/sizeof(kvs[0]))){fprintf(stderr,"bad variant\n");return 2;}
    kv=kvs[var];
    hA=malloc(asz);hB=malloc(bsz);hC=malloc(asz);
    if(verify){
        for(size_t j=0;j<asz/4;j++){seed=seed*1103515245u+12345u;hA[j]=(float)((seed>>16)%17)-8.0f;}
        for(size_t j=0;j<bsz/4;j++){seed=seed*1103515245u+12345u;hB[j]=(float)((seed>>16)%13)-6.0f;}
        ref=malloc(asz);
        for(int r=0;r<rows;r++)for(int c=0;c<n;c++){double s=0;
            for(int k=0;k<n;k++)s+=(double)hA[r*n+k]*hB[k*n+c]; ref[r*n+c]=(float)s;}
    } else {
        for(size_t j=0;j<asz/4;j++)hA[j]=1.0f;
        for(size_t j=0;j<bsz/4;j++)hB[j]=1.0f;
    }
    CHK(clGetPlatformIDs(1,&p,NULL),"plat");
    CHK(clGetDeviceIDs(p,CL_DEVICE_TYPE_GPU,1,&dev,NULL),"dev");
    ctx=clCreateContext(NULL,1,&dev,NULL,NULL,&err);CHK(err,"ctx");
    q=clCreateCommandQueue(ctx,dev,0,&err);CHK(err,"q");
    prog=clCreateProgramWithSource(ctx,1,&src,NULL,&err);CHK(err,"prog");
    if(clBuildProgram(prog,1,&dev,"",NULL,NULL)!=CL_SUCCESS){
        char*log=malloc(65536);size_t ls;clGetProgramBuildInfo(prog,dev,CL_PROGRAM_BUILD_LOG,65536,log,&ls);
        fprintf(stderr,"%s\n",log);return 1;}
    cl_kernel kk=clCreateKernel(prog,kv.kern,&err);CHK(err,"kern");
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
    if(kv.swap){g[0]=rows;g[1]=n;} else {g[0]=(n+kv.wdiv-1)/kv.wdiv;g[1]=(rows+kv.rdiv-1)/kv.rdiv;}
    l[0]=kv.l0;l[1]=kv.l1;
    g[0]=(g[0]+l[0]-1)/l[0]*l[0]; g[1]=(g[1]+l[1]-1)/l[1]*l[1];
    CHK(clEnqueueNDRangeKernel(q,kk,2,NULL,g,l,0,NULL,NULL),"warm");
    clFinish(q);
    for(i=0;i<reps;i++){t0=now_s();
        CHK(clEnqueueNDRangeKernel(q,kk,2,NULL,g,l,0,NULL,NULL),"ndr");
        clFinish(q);dt=now_s()-t0;if(dt<best)best=dt;}
    CHK(clEnqueueReadBuffer(q,bC,CL_TRUE,0,asz,hC,0,NULL,NULL),"rd");
    {   const char *verdict; long nbad=0; double worst=0;
        if(verify){ for(size_t j=0;j<asz/4;j++){
              double rel=fabs(hC[j]-ref[j])/(fabs(ref[j])+1.0);
              if(rel>worst)worst=rel; if(rel>1e-4)nbad++; }
            verdict = nbad? "WRONG":"VERIFIED";
        } else { for(size_t j=0;j<asz/4;j++) if(hC[j]!=(float)n){nbad++;break;}
            verdict = nbad? "BAD":"ok"; }
        printf("kern=%s n=%d rows=%d gflops=%.2f ms=%.2f %s",
               kv.kern,n,rows,2.0*rows*n*(double)n/best/1e9,best*1e3,verdict);
        if(verify) printf(" max_rel=%.2e bad=%ld",worst,nbad);
        printf("\n");
    }
    return 0;
}
