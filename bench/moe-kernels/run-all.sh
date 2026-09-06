#!/bin/bash
# Model-free MoE kernel bench driver. One node, one GPU, no engine.
#
# Each arm runs in its own throwaway container so a hard CUDA fault in one
# arm cannot take the others with it (the b12x arm produced exactly such a
# fault on its first epcompact attempt - see README.md).
#
# BENCH_DIR must contain moe_kernel_bench.py and is mounted at /bench.
# It needs about 100 MB; out/ and logs/ are created inside it.

set -u
BD=${BENCH_DIR:-/var/tmp/moebench}
IMG=${IMAGE:-harem/glm53-lil:t10}
CPUSET=${CPUSET:-10-14}          # optional CPU pinning; unset CPUSET to skip
MEMLIMIT=${MEMLIMIT:-16g}        # host RAM for the container; 16g was used
A2DQ=${A2DQ:-0.2360684}          # post-SiLU dequant global scale, from the gate

mkdir -p "$BD/out" "$BD/logs"
DOCKER_ARGS=(--rm --gpus all --ipc=host --memory="$MEMLIMIT" -v "$BD:/bench"
             --entrypoint python3)
[ -n "${CPUSET:-}" ] && DOCKER_ARGS+=(--cpuset-cpus "$CPUSET")

# telemetry sampler: SM clock, temperature, power, throttle reasons, host RAM
( while true; do
    echo "$(date +%s),$(nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.sm,clocks.max.sm,utilization.gpu,clocks_event_reasons.active --format=csv,noheader,nounits | tr -d ' '),$(awk '/MemAvailable/{print $2}' /proc/meminfo)"
    sleep 5
  done ) > "$BD/logs/telemetry.csv" 2>/dev/null &
TELE=$!
trap 'kill $TELE 2>/dev/null' EXIT

run () {  # layout arm sets tag forms...
  local LO=$1 ARM=$2 SETS=$3 TAG=$4; shift 4
  echo "=== $(date +%H:%M:%S)  layout $LO  arm $ARM  sets $SETS  forms $*"
  timeout 3600 docker run "${DOCKER_ARGS[@]}" "$IMG" /bench/moe_kernel_bench.py \
    time --layout "$LO" --arm "$ARM" --sets "$SETS" --forms "$@" \
    --M 8 64 1792 --routing uniform zipf --a2dq $A2DQ \
    --warmup 20 --iters 100 --rounds 3 \
    --out /bench/out/${TAG}.json > "$BD/logs/${TAG}.log" 2>&1
  echo "    rc=$?  $(grep -c 'FAILED' "$BD/logs/${TAG}.log") failed lines"
  grep -E '^  (dense|epcompact|nativeep)' "$BD/logs/${TAG}.log"
  awk '/MemAvailable/{print "    MemAvailable now: "$2" kB"}' /proc/meminfo
}

echo "###### PROBE (device, backends, ruler)"
docker run "${DOCKER_ARGS[@]}" "$IMG" /bench/moe_kernel_bench.py probe \
  > "$BD/logs/probe.log" 2>&1

echo "###### CORRECTNESS GATE (24 experts, production K/N/topk, M=64)"
# Add marlin-w4a8fp8 to --arms to reproduce the W4A8 refusal; it is left out
# here so the published gate JSONs contain only arms that produced numbers.
for LO in A B; do
  docker run "${DOCKER_ARGS[@]}" "$IMG" /bench/moe_kernel_bench.py gate \
    --layout $LO --M 64 --gate-experts 24 \
    --arms bf16 marlin-w4a16 vllm-cutlass-w4a4 fi-b12x-w4a4 \
    > "$BD/logs/gate-${LO}.log" 2>&1
  sed -n '/^{/,$p' "$BD/logs/gate-${LO}.log" > "$BD/out/gate-${LO}.json"
done

echo "###### LAYOUT A (EP: 96 local of 288, K=4096 N=2048 topk=8)"
run A marlin-w4a16      2 A-marlin-w4a16      dense epcompact nativeep
run A vllm-cutlass-w4a4 2 A-vllm-cutlass-w4a4 dense epcompact
run A fi-b12x-w4a4      2 A-fi-b12x-w4a4      dense
run A fi-b12x-w4a4      2 A-fi-b12x-w4a4-epcompact epcompact
run A bf16              1 A-bf16              dense epcompact nativeep

echo "###### LAYOUT B (no-EP, intermediate 2304/3: 288 local, K=4096 N=768 topk=8)"
run B marlin-w4a16      2 B-marlin-w4a16      dense
run B vllm-cutlass-w4a4 2 B-vllm-cutlass-w4a4 dense
run B fi-b12x-w4a4      2 B-fi-b12x-w4a4      dense
run B bf16              1 B-bf16              dense

echo "###### DONE $(date)"
echo "Tables: python3 make-tables.py > TABLES.md   (reads ./out/)"
