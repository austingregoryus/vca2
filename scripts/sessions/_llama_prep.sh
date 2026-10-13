#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
BASE=/tank-red/vmdata/vca
mkdir -p "$BASE" /tank-red/vmdata/vca/models
echo '=== install g++ on nodes (backup) ==='
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no'
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'apt-get install -y g++ make cmake 2>&1 | tail -5; g++ --version | head -1' || true
done

echo '=== zig? ==='
if [ ! -x "$BASE/zig/zig" ]; then
  echo 'download zig 0.13'
  cd /tmp
  wget -q -O zig.tar.xz https://ziglang.org/download/0.13.0/zig-linux-x86_64-0.13.0.tar.xz
  tar -xJf zig.tar.xz
  rm -rf "$BASE/zig"
  mv zig-linux-x86_64-0.13.0 "$BASE/zig"
fi
"$BASE/zig/zig" version

echo '=== llama.cpp clone ==='
if [ ! -d "$BASE/llama.cpp/.git" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$BASE/llama.cpp"
else
  git -C "$BASE/llama.cpp" fetch --depth 1 origin master
  git -C "$BASE/llama.cpp" reset --hard origin/master
fi
git -C "$BASE/llama.cpp" log -1 --oneline
ls "$BASE/llama.cpp/tools/rpc" 2>/dev/null | head
grep -r GGML_RPC "$BASE/llama.cpp/CMakeLists.txt" "$BASE/llama.cpp/ggml" --include=CMakeLists.txt | head
