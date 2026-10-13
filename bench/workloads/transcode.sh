# SPDX-License-Identifier: MIT
# Transcode capacity: how many simultaneous 1080p30 streams can be sustained?
#
# This is the workload a VCA2 is most plausibly bought for -- three fixed-
# function Quick Sync encoders that cost the host CPU nothing.
#
# WHAT IS MEASURED: aggregate encoded frames per second across all concurrent
# ffmpeg jobs. Not "is hardware faster than software" -- on this card it often
# is not (Gen9 VAAPI managed 140 fps against libx264 veryfast at 211 fps on
# the same node). The point is that the hardware path uses ~no CPU, so it is
# capacity you get *while the host does something else*.
#
# FAIRNESS NOTES, because encoder comparisons are easy to rig:
#   - Both sides encode the identical raw NV12 source at the same target
#     bitrate. Output sizes still differ (VAAPI rate control is not libx264's)
#     so this measures THROUGHPUT, not quality-per-bit. A VMAF comparison
#     would be a separate and much longer exercise.
#   - The host uses libx264 because its RTX 3070 is on `nouveau`, which
#     exposes no NVENC. With the proprietary driver the host would almost
#     certainly win outright -- that is stated in the verdict rather than
#     hidden.
#   - Jobs per side are scaled to available parallelism: host uses its core
#     count, each node runs one hardware encode (one encoder per node).

# FIXED-WINDOW DESIGN, and why it matters:
# Every configuration runs for exactly TC_RUN_SECS of WALL CLOCK, looping the
# source infinitely, and we count frames completed in that window.
#
# Two wrong versions preceded this one, both worth remembering:
#   1. Letting each side run to completion. Different durations made the
#      average-watt figures incomparable and produced a nonsense "the card
#      adds 50% throughput for 1 W" -- the longer run diluted its own average
#      with idle time.
#   2. Using ffmpeg's `-t`, which caps MEDIA duration, not wall clock. Each
#      side still ran for however long its own speed took (7.0 s vs 8.2 s).
#
# `timeout -s INT` is the fix: a real wall-clock limit on both sides. SIGINT
# rather than SIGKILL so ffmpeg flushes its final `frame=` stats line.
WL_DESCRIPTION="concurrent 1080p30 transcode capacity (fixed window, aggregate fps)"
WL_UNIT="fps"

TC_SECS="${TC_SECS:-10}"            # length of the generated source clip
TC_RUN_SECS="${TC_RUN_SECS:-30}"    # measurement window, IDENTICAL for every config
TC_SRC=/tmp/vcabench_src.yuv
TC_HOST_JOBS="${TC_HOST_JOBS:-4}"

wl_prepare() {
    command -v ffmpeg >/dev/null || {
        echo "    installing ffmpeg on the host"
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null 2>&1 || return 1
    }
    # One shared raw source so neither side gets an easier input.
    if [ ! -f "$TC_SRC" ]; then
        echo "    generating ${TC_SECS}s 1080p NV12 source"
        ffmpeg -hide_banner -loglevel error -f lavfi \
            -i "testsrc=size=1920x1080:rate=30:duration=${TC_SECS}" \
            -pix_fmt nv12 -c:v rawvideo -y "$TC_SRC" || return 1
    fi
    for i in 1 2 3; do
        node "$i" "test -f $TC_SRC" 2>/dev/null || {
            echo "    copying source to node $((i-1))"
            sshpass -p "$NODE_PW" scp -o StrictHostKeyChecking=no -q "$TC_SRC" "172.31.$i.1:$TC_SRC" || return 1
        }
    done
    return 0
}

# Parse ffmpeg's final "frame= N" from stderr -> frames encoded.
_frames_from() { grep -oE 'frame= *[0-9]+' "$1" 2>/dev/null | tail -1 | grep -oE '[0-9]+' || echo 0; }

_host_jobs() {
    local n="$1" i
    for i in $(seq 1 "$n"); do
        ( timeout -s INT "$TC_RUN_SECS" \
            ffmpeg -hide_banner -nostdin -loglevel error -stats \
            -stream_loop -1 -f rawvideo -pix_fmt nv12 -s 1920x1080 -r 30 -i "$TC_SRC" \
            -c:v libx264 -preset veryfast -b:v 5M -f null - ) \
            >"/tmp/vcabench_h$i.log" 2>&1 &
    done
}

_card_jobs() {
    local i
    for i in 1 2 3; do
        ( node "$i" "timeout -s INT $TC_RUN_SECS \
            ffmpeg -hide_banner -nostdin -loglevel error -stats \
            -vaapi_device /dev/dri/renderD128 \
            -stream_loop -1 -f rawvideo -pix_fmt nv12 -s 1920x1080 -r 30 -i $TC_SRC \
            -vf 'format=nv12,hwupload' -c:v h264_vaapi -b:v 5M -f null - " ) \
            >"/tmp/vcabench_n$i.log" 2>&1 &
    done
}

wl_run() {
    local cfg="$1" t0 t1 secs frames=0 i
    rm -f /tmp/vcabench_h*.log /tmp/vcabench_n*.log
    t0=$(date +%s.%N)
    case "$cfg" in
        host) _host_jobs "$TC_HOST_JOBS" ;;
        card) _card_jobs ;;
        both) _host_jobs "$TC_HOST_JOBS"; _card_jobs ;;
    esac
    wait
    t1=$(date +%s.%N)
    secs=$(echo "$t1 - $t0" | bc)

    case "$cfg" in
        host|both) for i in $(seq 1 "$TC_HOST_JOBS"); do
                       frames=$(( frames + $(_frames_from "/tmp/vcabench_h$i.log") )); done ;;
    esac
    case "$cfg" in
        card|both) for i in 1 2 3; do
                       frames=$(( frames + $(_frames_from "/tmp/vcabench_n$i.log") )); done ;;
    esac
    echo "scale=1; $frames / $secs" | bc
}

wl_cleanup() { rm -f /tmp/vcabench_h*.log /tmp/vcabench_n*.log; }
