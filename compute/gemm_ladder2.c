// SPDX-License-Identifier: MIT
/*
 * Round 2: explore around the lm8 winner (8 rows/work-item + local staging
 * of A). Varies rows-per-work-item, the k-tile depth TK, and adds a float2
 * column-width variant on top of local staging.
 */
#define CL_USE_DEPRECATED_OPENCL_1_2_APIS
#include <CL/cl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static const char *src =
"#define RBL(NAME,R,TK) \\\n"
"__kernel void NAME(int n, int rows, __global const float *A,\\\n"
"                   __global const float *B, __global float *C) {\\\n"
"  int c = get_global_id(0);\\\n"
"  int lc = get_local_id(0);\\\n"
"  int ls = get_local_size(0);\\\n"
"  int r0 = get_global_id(1) * R;\\\n"
"  __local float As[R][TK];\\\n"
"  float acc[R];\\\n"
"  for (int i = 0; i < R; i++) acc[i] = 0;\\\n"
"  for (int kb = 0; kb < n; kb += TK) {\\\n"
"    barrier(CLK_LOCAL_MEM_FENCE);\\\n"
"    for (int t = lc; t < R*TK; t += ls) {\\\n"
"      int i = t / TK, kk = t - i*TK;\\\n"
"      As[i][kk] = (r0+i < rows && kb+kk < n) ? A[(r0+i)*n + kb+kk] : 0.0f;\\\n"
"    }\\\n"
"    barrier(CLK_LOCAL_MEM_FENCE);\\\n"
"    if (c < n) {\\\n"
"      int kmax = (n - kb) < TK ? (n - kb) : TK;\\\n"
"      for (int kk = 0; kk < kmax; kk++) {\\\n"
"        float b = B[(kb+kk)*n+c];\\\n"
"        for (int i = 0; i < R; i++) acc[i] += As[i][kk] * b;\\\n"
"      }\\\n"
"    }\\\n"
"  }\\\n"
"  if (c < n) for (int i = 0; i < R; i++) if (r0+i < rows) C[(r0+i)*n+c] = acc[i];\\\n"
"}\n"
"RBL(lm4_32,4,32)\n"
"RBL(lm8_16,8,16)\n"
"RBL(lm8_32,8,32)\n"
"RBL(lm8_64,8,64)\n"
"RBL(lm8_128,8,128)\n"
"RBL(lm12_32,12,32)\n"
"RBL(lm16_16,16,16)\n"
"RBL(lm16_32,16,32)\n"
"RBL(lm16_64,16,64)\n"
"RBL(lm24_32,24,32)\n"
"RBL(lm32_32,32,32)\n"
/* local staging + float2 columns: each work-item does R rows x 2 cols */
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
"RBL2(l2r8_32,8,32)\n"
"RBL2(l2r16_32,16,32)\n"
"RBL2(l2r8_64,8,64)\n";

static double now_s(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
#define CHK(x,m) do{ if((x)!=CL_SUCCESS){fprintf(stderr,"FAIL %s err=%d L%d\n",m,(int)(x),__LINE__);exit(1);} }while(0)

static cl_context ctx; static cl_command_queue q; static cl_device_id dev;
static cl_mem bA,bB,bC; static float *hA,*hB,*hC; static int cap_n,cap_rows;

static void ensure(int n,int rows){
    cl_int err; size_t asz,bsz,i;
    if(n==cap_n&&rows==cap_rows)return;
    if(bA){clReleaseMemObject(bA);clReleaseMemObject(bB);clReleaseMemObject(bC);}
    free(hA);free(hB);free(hC);
    asz=(size_t)rows*n*4; bsz=(size_t)n*n*4;
    hA=malloc(asz);hB=malloc(bsz);hC=malloc(asz);
    for(i=0;i<asz/4;i++)hA[i]=1.0f;
    for(i=0;i<bsz/4;i++)hB[i]=1.0f;
    bA=clCreateBuffer(ctx,CL_MEM_READ_ONLY,asz,NULL,&err);CHK(err,"bA");
    bB=clCreateBuffer(ctx,CL_MEM_READ_ONLY,bsz,NULL,&err);CHK(err,"bB");
    bC=clCreateBuffer(ctx,CL_MEM_READ_WRITE,asz,NULL,&err);CHK(err,"bC");
    CHK(clEnqueueWriteBuffer(q,bA,CL_TRUE,0,asz,hA,0,NULL,NULL),"wA");
    CHK(clEnqueueWriteBuffer(q,bB,CL_TRUE,0,bsz,hB,0,NULL,NULL),"wB");
    cap_n=n;cap_rows=rows;
}

static double bench(cl_kernel kk,int n,int rows,int rdiv,int wdiv,size_t l0,size_t l1,int*okout){
    size_t g[2],l[2],*lp=NULL; int reps=3,i; double best=1e30,t0,dt; cl_int err;
    ensure(n,rows);
    CHK(clSetKernelArg(kk,0,sizeof(int),&n),"a0");
    CHK(clSetKernelArg(kk,1,sizeof(int),&rows),"a1");
    CHK(clSetKernelArg(kk,2,sizeof(cl_mem),&bA),"a2");
    CHK(clSetKernelArg(kk,3,sizeof(cl_mem),&bB),"a3");
    CHK(clSetKernelArg(kk,4,sizeof(cl_mem),&bC),"a4");
    g[0]=(size_t)((n+wdiv-1)/wdiv); g[1]=(size_t)((rows+rdiv-1)/rdiv);
    if(l0&&l1){l[0]=l0;l[1]=l1;lp=l;g[0]=(g[0]+l0-1)/l0*l0;g[1]=(g[1]+l1-1)/l1*l1;}
    err=clEnqueueNDRangeKernel(q,kk,2,NULL,g,lp,0,NULL,NULL);
    if(err!=CL_SUCCESS){*okout=-1;return 0;}
    if(clFinish(q)!=CL_SUCCESS){*okout=-1;return 0;}
    for(i=0;i<reps;i++){t0=now_s();
        CHK(clEnqueueNDRangeKernel(q,kk,2,NULL,g,lp,0,NULL,NULL),"ndr");
        clFinish(q);dt=now_s()-t0;if(dt<best)best=dt;}
    CHK(clEnqueueReadBuffer(q,bC,CL_TRUE,0,(size_t)rows*n*4,hC,0,NULL,NULL),"rd");
    {int bad=0;size_t j,tot=(size_t)rows*n;
     for(j=0;j<tot;j+=1013) if(hC[j]!=(float)n){bad=1;break;}
     if(hC[0]!=(float)n||hC[tot-1]!=(float)n)bad=1; *okout=!bad;}
    return 2.0*rows*n*(double)n/best/1e9;
}

struct V{const char*name;const char*kern;int rdiv,wdiv;};
static struct V vs[]={
    {"lm4  TK32","lm4_32",4,1},
    {"lm8  TK16","lm8_16",8,1},
    {"lm8  TK32","lm8_32",8,1},
    {"lm8  TK64","lm8_64",8,1},
    {"lm8  TK128","lm8_128",8,1},
    {"lm12 TK32","lm12_32",12,1},
    {"lm16 TK16","lm16_16",16,1},
    {"lm16 TK32","lm16_32",16,1},
    {"lm16 TK64","lm16_64",16,1},
    {"lm24 TK32","lm24_32",24,1},
    {"lm32 TK32","lm32_32",32,1},
    {"l2 r8  TK32","l2r8_32",8,2},
    {"l2 r16 TK32","l2r16_32",16,2},
    {"l2 r8  TK64","l2r8_64",8,2},
};

int main(int argc,char**argv){
    cl_platform_id p;cl_int err;cl_program prog;
    int n=argc>1?atoi(argv[1]):1536, rows=argc>2?atoi(argv[2]):512;
    size_t cand[][2]={{256,1},{128,1},{128,2},{64,4},{256,2}};
    int nc=sizeof(cand)/sizeof(cand[0]), nv=sizeof(vs)/sizeof(vs[0]), vi,ci,ok;
    cl_uint cu=0,fq=0; double bestall=0; char bd[128]="";
    CHK(clGetPlatformIDs(1,&p,NULL),"plat");
    CHK(clGetDeviceIDs(p,CL_DEVICE_TYPE_GPU,1,&dev,NULL),"dev");
    clGetDeviceInfo(dev,CL_DEVICE_MAX_COMPUTE_UNITS,sizeof(cu),&cu,NULL);
    clGetDeviceInfo(dev,CL_DEVICE_MAX_CLOCK_FREQUENCY,sizeof(fq),&fq,NULL);
    ctx=clCreateContext(NULL,1,&dev,NULL,NULL,&err);CHK(err,"ctx");
    q=clCreateCommandQueue(ctx,dev,0,&err);CHK(err,"q");
    prog=clCreateProgramWithSource(ctx,1,&src,NULL,&err);CHK(err,"prog");
    if(clBuildProgram(prog,1,&dev,"-cl-fast-relaxed-math",NULL,NULL)!=CL_SUCCESS){
        char*log=malloc(65536);size_t ls=0;
        clGetProgramBuildInfo(prog,dev,CL_PROGRAM_BUILD_LOG,65536,log,&ls);
        fprintf(stderr,"BUILD LOG:\n%s\n",log);exit(1);}
    printf("n=%d rows=%d  peak ~%.0f GFLOP/s\n",n,rows,cu*8.0*2.0*fq/1000.0);
    printf("%-14s","kernel");
    for(ci=0;ci<nc;ci++){char lb[16];snprintf(lb,sizeof(lb),"{%zu,%zu}",cand[ci][0],cand[ci][1]);printf(" %9s",lb);}
    printf("\n");
    for(vi=0;vi<nv;vi++){
        cl_kernel kk=clCreateKernel(prog,vs[vi].kern,&err);
        if(err!=CL_SUCCESS){printf("%-14s (missing)\n",vs[vi].name);continue;}
        printf("%-14s",vs[vi].name);
        for(ci=0;ci<nc;ci++){
            double g=bench(kk,n,rows,vs[vi].rdiv,vs[vi].wdiv,cand[ci][0],cand[ci][1],&ok);
            if(ok<0)printf(" %9s","-");
            else if(!ok)printf(" %8.0f!",g);
            else{printf(" %9.1f",g);
                 if(g>bestall){bestall=g;snprintf(bd,sizeof(bd),"%s local={%zu,%zu}",vs[vi].name,cand[ci][0],cand[ci][1]);}}
            fflush(stdout);}
        printf("\n");clReleaseKernel(kk);}
    printf("\nBEST %.1f GFLOP/s (%.1f%% of peak)  <- %s\nDONE\n",
           bestall,100.0*bestall/(cu*8.0*2.0*fq/1000.0),bd);
    return 0;
}
