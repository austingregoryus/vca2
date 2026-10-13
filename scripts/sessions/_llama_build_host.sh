#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
LL=/tank-red/vmdata/vca/llama.cpp
cd "$LL"
cmake -B /tank-red/vmdata/vca/llama-build-host \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_RPC=ON \
  -DGGML_RPC_RDMA=OFF \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_TOOLS_INSTALL=OFF
# find cli target
cmake --build /tank-red/vmdata/vca/llama-build-host --config Release -j"$(nproc)" \
  --target ggml-rpc-server llama-cli 2>&1 | tail -40
echo '=== binaries ==='
find /tank-red/vmdata/vca/llama-build-host -name 'ggml-rpc-server' -o -name 'llama-cli' | head
echo '=== rpc-server help ==='
RPC=$(find /tank-red/vmdata/vca/llama-build-host -name ggml-rpc-server | head -1)
"$RPC" --help 2>&1 | head -40
echo '=== ldd rpc ==='
ldd "$RPC" | head
