<!-- SPDX-License-Identifier: CC-BY-4.0 -->
> **Archived 2026-10-13.** The original planning notes from before any measurement (September 2026): a chat-assistant answer on whether and how the three VCA 2 nodes could be used as one machine. Kept for provenance. For what was measured, read [../STATUS.md](../STATUS.md) and the [top-level README](../../README.md).

Yes, but the important distinction is that the VCA2 is **not NVLink-like in software**, even though the hardware interconnect is much faster than the 1 Gb Ethernet-style networking you are using between the nodes.

Intel documents each VCA2 node as a **PCIe Gen3 x8 link**, with the host side capable of x16. That gives a theoretical payload bandwidth of roughly **7.9 GB/s per x8 link in each direction** before protocol/implementation overhead, which is dramatically faster than your current IP path. ([Intel CDR][1])

The catch is that the nodes do not automatically see one another as shared-memory peers. The stock VCASS stack exposes things like VOP networking and BlockIO, not a CUDA/NVLink-style unified fabric where one process can transparently address memory on all three CPUs.

## What can use all three today

The easiest options are distributed-compute frameworks that treat the nodes as separate machines:

* **MPI / OpenMPI** for CPU workloads
* **Ray** or **Dask** for Python jobs
* worker queues for scraping, transcoding, STT, TTS, etc.
* distributed inference frameworks

For AI specifically, `llama.cpp` now has an RPC backend that can expose a CPU device on multiple remote hosts and split model weights/KV cache across them. It can talk to several RPC servers at once. ([GitHub][2])

Conceptually:

```text
VCA node 0  CPU device
      \
VCA node 1  CPU device  ---> llama.cpp coordinator
      /
VCA node 2  CPU device
```

That would let one inference job use all three nodes.

However, if you use the existing VCA virtual Ethernet interfaces, the traffic still goes through the software network path, not directly at raw PCIe bandwidth.

## The really interesting experiment

You could potentially build a **custom PCIe transport** for the three nodes.

The VCA2 architecture already has:

* PLX PCIe switches
* DMA engines
* large BAR mappings
* VOP shared-device-page machinery
* BlockIO moving data over PCIe

Your own logs already showed the host allocating multi-gigabyte apertures and DMA channels for each node, so there is clearly a high-speed transport underneath VCASS.

A custom design could look like:

```text
                 PLX PCIe fabric
                /       |       \
           Node 0    Node 1    Node 2
              \         |         /
               shared host buffers
                      +
                     DMA
```

Instead of:

```text
Node 0 -> virtual Ethernet -> IP -> host -> IP -> Node 1
```

you could theoretically do:

```text
Node 0 -> PCIe DMA/shared BAR -> host memory -> Node 1
```

or possibly true peer-to-peer transfers if the switch topology and ACS/routing configuration permit them.

That would be much closer to an HPC interconnect, although still not equivalent to NVLink's coherent GPU-memory semantics.

## Best use cases for all three

I would rank the practical experiments like this:

1. **Distributed job execution**

   * easiest
   * Ray/Dask/MPI
   * great for scraping, encoding, batch inference

2. **llama.cpp RPC**

   * very interesting
   * one model split across all three CPUs
   * works even when only CPU devices are available ([GitHub][2])

3. **Parallel STT/TTS**

   * node 0 transcribes
   * node 1 synthesizes
   * node 2 handles orchestration
   * highest practical throughput

4. **MPI over the VCA network**

   * one computation spread across three nodes
   * easy benchmark of scaling

5. **Custom VOP/PCIe DMA transport**

   * hardest
   * potentially the most fun
   * could expose a high-bandwidth shared-buffer API to applications

## One subtle point

OpenVINO can split one model across multiple devices on **one machine**, including pipeline parallelism, but that feature is for devices visible to one OpenVINO runtime instance. It does not automatically make three VCA Linux nodes behave as one OpenVINO host. ([OpenVINO Documentation][3])

So for your card, distributed inference would need a network/RPC layer unless we build something lower-level.

## What I would try first

I would benchmark the existing inter-node transport before writing any driver code.

Run `iperf3` between:

```text
172.31.1.1
172.31.2.1
172.31.3.1
```

If VCASS's virtual network path is implemented efficiently over PCIe, you may already get **far more than 1 Gb/s**, despite the interfaces looking like ordinary Ethernet devices.

If that comes back at, say, several GB/s, then llama.cpp RPC or MPI becomes much more interesting immediately.

If you want, I can give you an exact **three-node iperf3 test matrix** and then we can see how much of that PCIe fabric VCASS is actually exposing.

[1]: https://cdrdv2-public.intel.com/840789/VCA2_HW_User_Guide.pdf "Intel® VCA 2 Product Specification and Hardware Guide"
[2]: https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md "llama.cpp/tools/rpc/README.md at master · ggml-org/llama.cpp · GitHub"
[3]: https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/hetero-execution.html "Heterogeneous Execution — OpenVINO™ documentation"
