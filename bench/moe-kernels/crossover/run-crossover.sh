#!/bin/bash
# FP4 crossover sweep driver - one node, model-free, engine untouched.
# Every stage is its own --rm container, capped at 6g, pinned off the engine cores.
set -u
BD=/var/tmp/xsweep
IMG=harem/glm53-lil:t10
A2DQ=0.2360684          # post-SiLU dequant global scale, from the 6 Sep gate
MEM_FLOOR_KB=$((6*1024*1024))
mkdir -p $BD/out $BD/logs $BD/torchext

memavail () { awk '/MemAvailable/{print $2}' /proc/meminfo; }

# --- telemetry + MemAvailable watchdog -------------------------------------
( while true; do
    echo "$(date +%s),$(nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.sm,clocks.max.sm,utilization.gpu,clocks_event_reasons.active --format=csv,noheader,nounits | tr -d ' '),$(memavail)"
    sleep 5
  done ) > $BD/logs/telemetry.csv 2>/dev/null &
TELE=$!
( LOW=0
  while true; do
    M=$(memavail)
    if [ "$M" -lt "$MEM_FLOOR_KB" ]; then
      LOW=$((LOW+1))
      echo "WATCHDOG: MemAvailable ${M} kB below floor (${LOW})" >> $BD/logs/watchdog.log
      if [ "$LOW" -ge 2 ]; then
        echo "WATCHDOG: killing xsweep container" >> $BD/logs/watchdog.log
        docker kill xsweep-run >/dev/null 2>&1
        LOW=0
      fi
    else
      LOW=0
    fi
    sleep 5
  done ) &
WD=$!
trap "kill $TELE $WD 2>/dev/null" EXIT

run () {  # tag  mode  extra-args...
  local TAG=$1 MODE=$2; shift 2
  echo "=== $(date +%H:%M:%S)  $TAG ($MODE)   MemAvailable $(( $(memavail) / 1024 )) MiB"
  timeout 5400 docker run --rm --name xsweep-run --gpus all --ipc=host \
    --cpuset-cpus 10-14 --memory=6g \
    -e TORCH_EXTENSIONS_DIR=/bench/torchext -e TORCH_CUDA_ARCH_LIST=12.1 \
    -v $BD:/bench --entrypoint python3 $IMG /bench/xsweep.py $MODE \
    --a2dq $A2DQ --mem-floor 6 --out /bench/out/${TAG}.json "$@" \
    > $BD/logs/${TAG}.log 2>&1
  echo "    rc=$?   MemAvailable now $(( $(memavail) / 1024 )) MiB"
  tail -3 $BD/logs/${TAG}.log | sed 's/^/    /'
}

MS="8 16 32 64 128 256 512 1024 1792 4096"
SMOKE="--M 8 1792 --routing uniform --forms dense epcompact --warmup 2 --iters 3 --rounds 1"

if [ "${SMOKE_ONLY:-0}" = "1" ] || [ "${DO_SMOKE:-1}" = "1" ]; then
  echo "###### -1. SMOKE (API check, ~2 min; every arm + the decomposition)"
  run smoke-marlin  time  --arm marlin-w4a16      $SMOKE
  run smoke-cutlass time  --arm vllm-cutlass-w4a4 $SMOKE
  run smoke-b12x    time  --arm fi-b12x-w4a4      $SMOKE --b12x-maxtok matched
  run smoke-split   split                         $SMOKE
  run smoke-bf16    time  --arm bf16              $SMOKE
  for f in smoke-marlin smoke-cutlass smoke-b12x smoke-split smoke-bf16; do
    python3 - "$BD/out/$f.json" <<'EOF'
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception as e:
    print("  SMOKE %s: NO JSON (%s)"%(sys.argv[1],e)); sys.exit(0)
bad=[r for r in d["runs"] if r.get("status")=="FAILED"]
print("  SMOKE %-14s runs=%d failed=%d %s"%(d.get("arm"),len(d["runs"]),len(bad),
      bad[0]["error"][:90] if bad else "OK"))
EOF
  done
  [ "${SMOKE_ONLY:-0}" = "1" ] && { echo "SMOKE_ONLY - stopping"; exit 0; }
fi

echo "###### 0. RULERS (three ways)"
run ruler ruler --ruler-gib 2.0

echo "###### 1. MAIN SWEEP - Layout A, forms dense + epcompact, routing uniform + zipf"
run marlin        time --arm marlin-w4a16      --M $MS
run cutlass       time --arm vllm-cutlass-w4a4 --M $MS
run b12x-matched  time --arm fi-b12x-w4a4      --M $MS --b12x-maxtok matched --tag matched
run b12x-fixed2k  time --arm fi-b12x-w4a4      --M $MS --b12x-maxtok fixed   --tag fixed2048

echo "###### 2. GEMM-ONLY BOUND (cutlass FP4 decomposed)"
run split         split --M $MS

echo "###### 3. BF16 REFERENCE (1 weight set; heaviest, runs last)"
FREE=$(( $(memavail) / 1024 / 1024 ))
if [ "$FREE" -ge 10 ]; then
  run bf16 time --arm bf16 --M $MS
else
  echo "    SKIPPED bf16: only ${FREE} GiB available, need >= 10"
fi

echo "###### DONE $(date)"
