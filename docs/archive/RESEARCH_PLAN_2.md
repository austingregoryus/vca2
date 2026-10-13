<!-- SPDX-License-Identifier: CC-BY-4.0 -->
> **Archived 2026-10-13.** The second round of planning notes from before any measurement (September 2026): a proposed native PCIe offload runtime for the three CPU groups and three GPUs. Kept for provenance. For what was actually built and measured, read [../STATUS.md](../STATUS.md) and the [top-level README](../../README.md).

**Yes. The target should be one application that can use all three CPU groups and all three GPUs through a native PCIe command-and-data interface, without using virtual Ethernet for the computation.**

My previous PoCL-Remote suggestion does not meet that requirement by itself. Its normal transport uses TCP connections. We could reuse parts of its compute machinery, but a native VCA transport would require development, not a configuration switch. ([PoCL][1])

I would organize the project around **a CPU/GPU offload runtime**, with the direct PCIe interface as its foundation.

## 1. What the application should see

The proposed runtime would expose six logical compute targets:

```text
VCA2
├── node0.cpu    Xeon worker pool
├── node0.gpu    Iris Pro P580
├── node1.cpu    Xeon worker pool
├── node1.gpu    Iris Pro P580
├── node2.cpu    Xeon worker pool
└── node2.gpu    Iris Pro P580
```

Each CPU target represents the node’s compute capacity, not just a thread that feeds the GPU. Your processor model has four cores, eight threads, AVX2, and an integrated P580, so both types of execution are relevant. ([Intel][2])

One host application could submit a calculation to an individual target, explicitly divide it among several targets, or let our scheduler distribute supported work across all six.

**The CPU workers would perform useful computation alongside the GPUs, rather than merely act as GPU controllers.**

The architecture I would build is:

```text
                 Your application on vca-host
                            │
                 Unified compute API: libvca
                            │
            Buffers, job queues, dependencies, events
                            │
              Native VCA PCIe transport driver
                            │
              PCIe memory windows / DMA / notifications
                            │
             ┌──────────────┼──────────────┐
             │              │              │
          VCA node 0     VCA node 1     VCA node 2
          Local worker   Local worker   Local worker
             │              │              │
          CPU + GPU      CPU + GPU      CPU + GPU
```

Those library and component names are a proposed design, not existing software.

The node-side Linux installations would stay. They would host the executors and local GPU drivers, but the application would not communicate with them through SSH commands, HTTP, or TCP.

## 2. What “native PCIe” means here

Your existing VOP networking already travels over PCIe. What you want to bypass is the **networking abstraction and software path**, not change the physical connection. The VCA source identifies VOP as Virtio Over PCIe and implements descriptor queues, memory mappings, and peer notifications underneath it. 

The new compute path would instead work like this:

```text
Application prepares a registered buffer
                    ↓
Submits a job descriptor into a command queue
                    ↓
PCIe notification tells the node work is available
                    ↓
Node executes it on CPU or GPU
                    ↓
Completion queue reports the result
```

Large payloads would use DMA transfers where supported by the driver path. Small commands would use shared queue structures accessible through PCIe memory windows.

These are established building blocks of non-transparent PCIe bridges: translated memory windows provide access to peer memory, and doorbells provide peer notifications. Linux has an NTB framework describing these mechanisms, although that does not mean its generic drivers can replace your VCA driver unchanged. ([Linux Kernel Documentation][3])

**No Ethernet frames, IP addresses, TCP streams, or network listeners would be required for submitting compute jobs or moving their buffers.**

You could still keep the virtual Ethernet interfaces for SSH and package installation. That would be an administrative convenience, separate from the compute interface.

### We do not have to discard everything called “VirtIO”

A non-network queue implementation could reuse useful parts of VOP without using `virtio-net`. The goal is to remove networking from the computation path, not unnecessarily rewrite working descriptor and notification code.

I would extend the existing driver stack with a **separate non-network endpoint**, rather than attach a competing driver that independently reprograms the same bridge resources.

## 3. The software we actually need to build

I would separate it into four components:

| Component                   | Responsibility                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------ |
| **Native transport driver** | Registered buffers, PCIe mappings, transfers, command/completion queues, and notifications |
| **Host library**            | Device enumeration, buffer placement, job submission, and result collection                |
| **Node executor**           | Receives commands over the native endpoint and dispatches work locally                     |
| **Compute backends**        | Native CPU execution and GPU execution through a compatible graphics runtime               |

### CPU execution

The first CPU backend should execute **registered native operations** in a worker pool. For example, a matrix operation, image transformation, reduction, or application-specific function compiled for the node.

That avoids forcing all CPU work into a GPU-style language. Later, an OpenCL CPU backend is another option; PoCL already provides a multithreaded CPU implementation. ([PoCL][4])

For your Go projects, a Go application could call the host library, and a node worker could execute a compiled Go operation. It would still need an explicit input/output contract. An arbitrary Go function and its pointers cannot simply be sent to another running process unchanged.

### GPU execution

The GPU executor would call the node’s local OpenCL implementation. Intel documents Skylake support in its legacy compute-runtime packages, which gives us a candidate backend, but compatibility with your exact VCA kernel and installed userspace remains to be demonstrated. 

**Both execution types would receive commands through the same native PCIe interface.** Only the final execution backend differs.

## 4. One application does not require one shared Linux address space

We should make the application interface unified while keeping memory placement explicit.

For example, the application could work with a logical buffer handle:

```text
buffer_42
    current location: node1
    available to: node1 CPU executor
    GPU representation: created when needed
    transfer dependencies: tracked by runtime
```

The runtime would move or replicate that buffer when another node needs it.

That is different from adding the VCA CPUs to the Proxmox host’s ordinary scheduler. The processor is a single-socket design with no QPI links, and the NTB connection joins separate memory systems rather than making them one conventional multiprocessor machine. ([Intel][2])

**The achievable abstraction is “one application with multiple compute devices,” not “ordinary host threads automatically running on the remote Xeons.”**

Similarly, “native PCIe” and “zero-copy GPU access” are separate accomplishments:

| Capability                                                   | Initial design                                |
| ------------------------------------------------------------ | --------------------------------------------- |
| Commands bypass TCP/IP                                       | Required                                      |
| Bulk data bypasses TCP/IP                                    | Required                                      |
| CPU uses a node-local received buffer                        | Required                                      |
| GPU receives the same logical input                          | Required                                      |
| GPU directly uses the transport’s allocation without staging | Later optimization, subject to driver support |
| One node directly transfers to another without host staging  | Separate topology and driver investigation    |

OpenCL’s `CL_MEM_USE_HOST_PTR`, for example, does not universally guarantee zero-copy execution; implementations may maintain a device-side copy. We would verify actual behavior rather than infer it from a flag. ([Khronos Registry][5])

For cross-node dependencies, the initial implementation could relay buffers through host memory while still using native PCIe throughout. **Keeping peer transfers entirely on the card is a further target, not something to claim before establishing the routing and mappings.**

## 5. The first demonstration should use all six targets

I would build **one distributed matrix multiplication or tiled image operation**, with both CPU and GPU implementations.

For matrix multiplication:

```text
One input calculation: C = A × B

Node 0 CPU: assigned output tiles
Node 0 GPU: assigned output tiles

Node 1 CPU: assigned output tiles
Node 1 GPU: assigned output tiles

Node 2 CPU: assigned output tiles

Node 2 GPU: assigned output tiles

Host application: collects one completed C
```

Each endpoint computes a distinct portion of the **same result**. That demonstrates what you are asking for, rather than merely launching six unrelated processes.

The scheduler should not divide the work into six equal pieces by assumption. It should measure throughput and assign suitable tile sizes, while retaining reusable inputs on the nodes.

For repeated operations, the proposed usage would be:

```text
Upload persistent data once
        ↓
Submit many computations referencing that data
        ↓
Keep intermediate results where they are needed
        ↓
Retrieve only the required output
```

The success criterion would include running the compute test with **no VCA IP connectivity available to its processes**. The native device interface should be sufficient. That would prove we did not accidentally build another network wrapper.

## 6. The shortest implementation path

I would take these steps, using your working installation as the base:

### First: add one non-network host-to-node channel

Implement a small command/completion interface and a bounded transfer buffer on node 0. Demonstrate a round trip, then a CPU calculation whose inputs and outputs pass through that channel.

This is the first new capability, not another general reliability exercise.

There is relevant code to build on. The public VCA bridge implementation includes address-translation handling and peer-memory mapping functions such as `plx_ioremap()`. Those are better starting points than inventing PLX register programming from scratch. 

However, the previously discussed `VCA_VIRTIO_COPY_DESC` operation returns `-EFAULT` in the public source I inspected. It is not a ready-made transfer API we can simply start calling. We need to compare your exact source revision and implement a deliberate interface. 

### Second: put CPU and GPU execution behind that channel

The same submission format would select a CPU operation or GPU operation. Start with one simple operation implemented both ways and verify equivalent results.

The kernel driver should manage transport, not execute application code. Application kernels and functions belong in the node’s userspace executor.

### Third: expand to all three nodes

Add enumeration, independent queues, concurrent execution, and one host-side scheduler. Then run the six-target demonstration above.

### Fourth: optimize and integrate

Only after that works, improve transfer overlap, buffer reuse, and GPU-buffer interoperability. Then add an integration for the application you actually want to accelerate.

An OpenCL-compatible frontend or a model-runtime backend could make the system useful to existing software, but exposing devices alone does not make every existing application distribute work across them.

The driver work needs explicit buffer lifetimes, bounds checks, synchronization, and safe teardown. PCIe DMA addresses are not ordinary application pointers, and even coherent mappings still require correct memory ordering. Those are the essential correctness requirements of this new interface, not a request to repeat your existing stress tests. ([Linux Kernel Documentation][6])

---

**The project target is therefore: a native PCIe CPU/GPU offload runtime, exposing six compute endpoints to one application.** The first code milestone should be a host-submitted CPU calculation over a new non-network channel; the first complete demonstration should combine all three CPUs and all three GPUs into one result. Neither milestone needs a Proxmox rewrite, a new cluster manager, or TCP-based remote execution.

[1]: https://portablecl.org/docs/html/remote.html "Remote Driver — Portable Computing Language (PoCL) 7.2 documentation"
[2]: https://www.intel.com/content/www/us/en/products/sku/93741/intel-xeon-processor-e31585l-v5-8m-cache-3-00-ghz/specifications.html "Intel® Xeon® Processor E3-1585L v5"
[3]: https://docs.kernel.org/driver-api/ntb.html "NTB Drivers — The Linux Kernel documentation"
[4]: https://portablecl.org/docs/html/cpu.html "CPU device drivers — Portable Computing Language (PoCL) 7.2 documentation"
[5]: https://registry.khronos.org/OpenCL/specs/3.0-unified/html/OpenCL_API.html "The OpenCL™ Specification"
[6]: https://docs.kernel.org/core-api/dma-api-howto.html "Dynamic DMA mapping Guide — The Linux Kernel documentation"
