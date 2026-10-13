#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
ZIG=/tank-red/vmdata/vca/zig/zig
LL=/tank-red/vmdata/vca/llama.cpp
W=/tank-red/vmdata/vca/zig-wrap
mkdir -p "$W"
cat > "$W/cc" <<'EOF'
#!/bin/bash
exec /tank-red/vmdata/vca/zig/zig cc -target x86_64-linux-gnu.2.23 -fno-sanitize=undefined "$@"
EOF
cat > "$W/c++" <<'EOF'
#!/bin/bash
exec /tank-red/vmdata/vca/zig/zig c++ -target x86_64-linux-gnu.2.23 -fno-sanitize=undefined "$@"
EOF
chmod +x "$W/cc" "$W/c++"
cmake -B /tank-red/vmdata/vca/llama-build-vca \
  -DCMAKE_C_COMPILER="$W/cc" \
  -DCMAKE_CXX_COMPILER="$W/c++" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_RPC=ON \
  -DGGML_RPC_RDMA=OFF \
  -DGGML_NATIVE=OFF \
  -DGGML_AVX=ON \
  -DGGML_AVX2=ON \
  -DGGML_FMA=ON \
  -DGGML_F16C=ON \
  -DGGML_OPENMP=OFF \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -S "$LL"
cmake --build /tank-red/vmdata/vca/llama-build-vca --config Release -j"$(nproc)" --target ggml-rpc-server
echo '=== vca rpc binary ==='
ls -lh /tank-red/vmdata/vca/llama-build-vca/bin/ggml-rpc-server
file /tank-red/vmdata/vca/llama-build-vca/bin/ggml-rpc-server
ldd /tank-red/vmdata/vca/llama-build-vca/bin/ggml-rpc-server || true
