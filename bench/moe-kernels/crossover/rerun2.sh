#!/bin/bash
set -u
BD=/var/tmp/xsweep
IMG=harem/glm53-lil:t10
A2DQ=0.2360684
MEM_FLOOR_KB=$((6*1024*1024))
memavail () { awk '/MemAvailable/{print $2}' /proc/meminfo; }
( while true; do
    echo "$(date +%s),$(nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.sm,clocks.max.sm,utilization.gpu,clocks_event_reasons.active --format=csv,noheader,nounits | tr -d ' '),$(memavail)"
    sleep 5; done ) >> $BD/logs/telemetry.csv 2>/dev/null &
TELE=$!
( LOW=0; while true; do
    M=$(memavail)
    if [ "$M" -lt "$MEM_FLOOR_KB" ]; then LOW=$((LOW+1))
      echo "WATCHDOG: MemAvailable ${M} kB below floor (${LOW})" >> $BD/logs/watchdog.log
      [ "$LOW" -ge 2 ] && { docker kill xsweep-run >/dev/null 2>&1; LOW=0; }
    else LOW=0; fi
    sleep 5; done ) &
WD=$!
trap "kill $TELE $WD 2>/dev/null" EXIT
run () { local TAG=$1 MODE=$2; shift 2
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
MS2="8 16 32 64 128 256 512 1024 1792"
echo "###### 2b. GEMM-ONLY BOUND (M capped at 1792: 4096 exceeds production MNBT 2048)"
run split split --M $MS2
echo "###### 3b. BF16 REFERENCE"
run bf16 time --arm bf16 --M $MS2
echo "###### DONE $(date)"
