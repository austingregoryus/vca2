// SPDX-License-Identifier: MIT
/*
 * Validate the fast GEMM kernels against a CPU reference with NON-UNIFORM
 * data.
 *
 * Why: every GEMM check in cl_daemon.c fills A=1, B=1 and asserts C==n. With
 * all values identical that cannot detect reading the wrong row of A, a race
 * on a __local tile, or a transposed access -- only a wrong k-loop length.
 * Here A[r][k] and B[k][c] are distinct pseudo-random values and the result
 * is compared elementwise against a CPU GEMM.
 *
 * Also proves the __local kernels are only correct when local size in dim 1
 * is 1: As[][] is per-workgroup but r0 = gid(1)*R, so a dim-1 local size > 1
 * puts two different row blocks on one tile. With A=1 that race is invisible.
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
"RBL2(l2r8_64,8,64)\n"
"RBL2(l2r8_32,8,32)\n"
"__kernel void naive_rc(int n,int rows,__global const float*A,\n"
"                       __global const float*B,__global float*C){\n"
"  int r=get_global_id(0),c=get_global_id(1);\n"
"  if(r>=rows||c>=n)return; float s=0;\n"
"  for(int k=0;k<n;k++) s+=A[r*n+k]*B[k*n+c];\n"
"  C[r*n+c]=s;\n}\n";

static double now_s(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
#define CHK(x,m) do{ if((x)!=CL_SUCCESS){fprintf(stderr,"FAIL %s err=%d L%d\n",m,(int)(x),__LINE__);exit(1);} }while(0)

int main(int argc,char**argv){
    int n=argc>1?atoi(argv[1]):512, rows=argc>2?atoi(argv[2]):170;
    cl_platform_id p;cl_device_id dev;cl_int err;cl_context ctx;cl_command_queue q;cl_program prog;
    cl_mem bA,bB,bC; float *hA,*hB,*hC,*ref;
    size_t asz=(size_t)rows*n*4, bsz=(size_t)n*n*4;
    size_t g[2],l[2]; int r,c,k; unsigned seed=12345;

    hA=malloc(asz);hB=malloc(bsz);hC=malloc(asz);ref=malloc(asz);
    /* distinct small values so wrong-row / race / transpose all show up */
    for(size_t i=0;i<asz/4;i++){seed=seed*1103515245u+12345u;hA[i]=(float)((seed>>16)%17)-8.0f;}
    for(size_t i=0;i<bsz/4;i++){seed=seed*1103515245u+12345u;hB[i]=(float)((seed>>16)%13)-6.0f;}
    printf("CPU reference %dx%d ... ",rows,n);fflush(stdout);
    for(r=0;r<rows;r++)for(c=0;c<n;c++){double s=0;
        for(k=0;k<n;k++)s+=(double)hA[r*n+k]*hB[k*n+c];
        ref[r*n+c]=(float)s;}
    printf("done\n");

    CHK(clGetPlatformIDs(1,&p,NULL),"plat");
    CHK(clGetDeviceIDs(p,CL_DEVICE_TYPE_GPU,1,&dev,NULL),"dev");
    ctx=clCreateContext(NULL,1,&dev,NULL,NULL,&err);CHK(err,"ctx");
    q=clCreateCommandQueue(ctx,dev,0,&err);CHK(err,"q");
    prog=clCreateProgramWithSource(ctx,1,&src,NULL,&err);CHK(err,"prog");
    if(clBuildProgram(prog,1,&dev,"",NULL,NULL)!=CL_SUCCESS){
        char*log=malloc(65536);size_t ls;clGetProgramBuildInfo(prog,dev,CL_PROGRAM_BUILD_LOG,65536,log,&ls);
        fprintf(stderr,"%s\n",log);exit(1);}
    bA=clCreateBuffer(ctx,CL_MEM_READ_ONLY,asz,NULL,&err);CHK(err,"bA");
    bB=clCreateBuffer(ctx,CL_MEM_READ_ONLY,bsz,NULL,&err);CHK(err,"bB");
    bC=clCreateBuffer(ctx,CL_MEM_READ_WRITE,asz,NULL,&err);CHK(err,"bC");
    CHK(clEnqueueWriteBuffer(q,bA,CL_TRUE,0,asz,hA,0,NULL,NULL),"wA");
    CHK(clEnqueueWriteBuffer(q,bB,CL_TRUE,0,bsz,hB,0,NULL,NULL),"wB");

    struct T{const char*kern;const char*label;int wdiv,rdiv,swap;size_t l0,l1;} ts[]={
      {"naive_rc","naive_rc      local={1,128}",1,1,1,1,128},
      {"l2r8_32", "l2 r8 TK32    local={256,1}",2,8,0,256,1},
      {"l2r8_64", "l2 r8 TK64    local={256,1}",2,8,0,256,1},
      {"l2r8_64", "l2 r8 TK64    local={128,2}  <- dim1>1",2,8,0,128,2},
      {"l2r8_64", "l2 r8 TK64    local={64,4}   <- dim1>1",2,8,0,64,4},
    };
    for(unsigned t=0;t<sizeof(ts)/sizeof(ts[0]);t++){
        cl_kernel kk=clCreateKernel(prog,ts[t].kern,&err);CHK(err,"kern");
        CHK(clSetKernelArg(kk,0,sizeof(int),&n),"a0");
        CHK(clSetKernelArg(kk,1,sizeof(int),&rows),"a1");
        CHK(clSetKernelArg(kk,2,sizeof(cl_mem),&bA),"a2");
        CHK(clSetKernelArg(kk,3,sizeof(cl_mem),&bB),"a3");
        CHK(clSetKernelArg(kk,4,sizeof(cl_mem),&bC),"a4");
        if(ts[t].swap){g[0]=rows;g[1]=n;}
        else{g[0]=(n+ts[t].wdiv-1)/ts[t].wdiv;g[1]=(rows+ts[t].rdiv-1)/ts[t].rdiv;}
        l[0]=ts[t].l0;l[1]=ts[t].l1;
        g[0]=(g[0]+l[0]-1)/l[0]*l[0]; g[1]=(g[1]+l[1]-1)/l[1]*l[1];
        memset(hC,0,asz);
        CHK(clEnqueueWriteBuffer(q,bC,CL_TRUE,0,asz,hC,0,NULL,NULL),"clr");
        err=clEnqueueNDRangeKernel(q,kk,2,NULL,g,l,0,NULL,NULL);
        if(err!=CL_SUCCESS){printf("  %-38s ENQUEUE_ERR %d\n",ts[t].label,err);continue;}
        clFinish(q);
        double t0=now_s();
        CHK(clEnqueueNDRangeKernel(q,kk,2,NULL,g,l,0,NULL,NULL),"ndr");
        clFinish(q); double dt=now_s()-t0;
        CHK(clEnqueueReadBuffer(q,bC,CL_TRUE,0,asz,hC,0,NULL,NULL),"rd");
        double worst=0; long nbad=0;
        for(size_t i=0;i<asz/4;i++){
            double rel=fabs(hC[i]-ref[i])/(fabs(ref[i])+1.0);
            if(rel>worst)worst=rel;
            if(rel>1e-4)nbad++;
        }
        printf("  %-38s %7.1f GFLOP/s  max_rel=%.2e  bad=%ld  %s\n",
               ts[t].label, 2.0*rows*n*(double)n/dt/1e9, worst, nbad,
               nbad==0?"VERIFIED":"*** WRONG ***");
        fflush(stdout);
        clReleaseKernel(kk);
    }
    printf("DONE\n");
    return 0;
}
