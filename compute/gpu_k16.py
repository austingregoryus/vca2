#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
from __future__ import print_function
import socket, threading, time
NODES = [("node0","172.31.1.1"),("node1","172.31.2.1"),("node2","172.31.3.1")]

def splits(n):
    a,b=n//3,n//3
    return [(0,a),(a,b),(a+b,n-a-b)]

class Sess(object):
    def __init__(self, ip):
        self.s=socket.create_connection((ip,9200),timeout=10)
        self.s.settimeout(180)
        self.f=self.s.makefile("rw")
        self.hello=self.f.readline().strip()
    def send(self, line):
        self.f.write(line+"\n"); self.f.flush()
        return self.f.readline().strip()
    def close(self):
        try: self.send("QUIT")
        except Exception: pass
        self.s.close()

def par(sessions, cmd, n):
    sp=splits(n); got=[None]*3
    def one(i):
        r0,rows=sp[i]
        got[i]=sessions[i][1].send("%s %d %d %d"%(cmd,n,r0,rows))
    t0=time.time()
    ths=[threading.Thread(target=one,args=(i,)) for i in range(3)]
    for th in ths: th.start()
    for th in ths: th.join()
    gs,ok=[],True
    for i in range(3):
        r=got[i]; print(" ",sessions[i][0], r)
        ok=ok and ("expect=%.1f"%float(n) in r)
        gs.append(float(r.split("gflops=")[1].split()[0]) if "gflops=" in r else 0)
    return time.time()-t0, gs, ok

def main():
    sessions=[]
    for label,ip in NODES:
        se=Sess(ip); print("connected",label,se.hello); sessions.append((label,se))
    all_ok=True
    for n in (384,1024,1408,1536,1600):
        for cmd,tag in (("GEMM","naive"),("GEMMK16","k16")):
            wall,gs,ok=par(sessions,cmd,n)
            all_ok=all_ok and ok
            print("%s n=%d wall=%.3f sum=%.1f %s"%(tag,n,wall,sum(gs),"OK" if ok else "FAIL"))
    for _,se in sessions: se.close()
    print("K16_OK" if all_ok else "K16_FAIL")
    if not all_ok: raise SystemExit(1)
if __name__=="__main__":
    main()
