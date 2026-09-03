#!/bin/bash
# Post-install self-check. Run this after the engine is up and answering, and
# compare what you get against the expected ranges printed alongside each step.
#
#   ./run-audit.sh                 all checks
#   ./run-audit.sh health kv       only those two
#
# Checks: health  kv  probe  code  c1  category  memory
#
# Every expected range below was measured on our own three-node cluster with
# the production configuration and is cited in audit/README.md, which names the
# source table for each number. Ranges are what we saw across repeats — a
# reading just outside one is not automatically a fault, but a reading far
# outside one means something in your stack differs from ours, and the audit
# tells you WHICH thing.
#
# Settings all of these assume:
#   image harem/glm53-lil:t10, TP=3 + expert parallelism + marlin MoE,
#   NVFP4 weights, KV dtype fp8, no KV pin, block size 256, CUDA graphs ON,
#   DFlash2 speculative decoding k=7, gpu-memory-utilization 0.88,
#   max-num-seqs 8, max-num-batched-tokens 2048, thinking ON at effort low,
#   temperature 0.
#
# Anything you publish from this script must carry those settings with it.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
SC="$D/../scripts"
. "$SC/lib-cluster.sh"

WHAT="${*:-health kv probe code c1 category memory}"
want() { case " $WHAT " in *" $1 "*) return 0;; *) return 1;; esac; }
hdr()  { echo; echo "--- $1"; }
FAILED=0

# ---------------------------------------------------------------- health ----
if want health; then
  hdr "health            expect: HTTP 200 within a few milliseconds"
  if curl -s -m 10 -o /dev/null -w '  /health -> HTTP %{http_code} in %{time_total}s\n' "$API/health"; then
    curl -s -m 10 "$API/v1/models" | head -c 300 | sed 's/^/  /'; echo
  else
    echo "  FAIL: no answer at $API"; FAILED=1
  fi
fi

# -------------------------------------------------------------------- KV ----
if want kv; then
  hdr "KV pool           expect: 4,321,739 tokens at gpu-mem 0.88 (3,881,159 at 0.85)"
  echo "  (memory ladder, audit/README.md; a pool within a few percent is fine,"
  echo "   a pool 30%+ smaller usually means the drafter or the page layout differs)"
  $SSH "$HEAD_HOST" "docker logs $CONTAINER 2>&1 | grep -E 'GPU KV cache size|Available KV cache memory|Maximum concurrency' | head -3 | cut -c40-330" \
    | sed 's/^/  /' || { echo "  FAIL: could not read the engine log on $HEAD_HOST"; FAILED=1; }
fi

# --------------------------------------------------------------- probe ------
if want probe; then
  hdr "correctness probe expect: 10/10, empty-content 0"
  python3 "$SC/correctness-probe.py" "$API" | sed 's/^/  /' || FAILED=1
fi

# ---------------------------------------------------------------- code ------
if want code; then
  hdr "code exam         expect: 12/12"
  echo "  (this EXECUTES model-written python locally; see scripts/code-exam.py)"
  python3 "$SC/code-exam.py" "$API" | sed 's/^/  /' || FAILED=1
fi

# ------------------------------------------------------------------ C1 ------
if want c1; then
  hdr "cold/warm C1      expect: 54-63 tok/s, acceptance 57-69% (shipped English prompt)"
  echo "  (our published 43-47 tok/s / 44-48% came from the Turkish version of the"
  echo "   same prompt; the drafter does noticeably better in English. See"
  echo "   results/speed/cold-warm-c1-english-prompt.txt)"
  echo "  (on a freshly started engine the first of the three is genuinely COLD"
  echo "   and will be lower; that is the point of running it first)"
  python3 "$SC/cold-warm-c1.py" "$API" | sed 's/^/  /' || FAILED=1
fi

# ------------------------------------------------------------ category ------
if want category; then
  hdr "category speed    expect (C1 mean decode / C4 total):"
  echo "    code   C1 45-48 tok/s      C4 total 78-80 tok/s"
  echo "    json   C1 ~52 tok/s        C4 total 80-91 tok/s"
  echo "    math   C1 ~57 tok/s        C4 total ~80 tok/s"
  echo "    prose  C1 21-22 tok/s      acceptance ~13% -- the drafter barely fires on prose"
  echo "  (~14 min. Our published numbers used a mixed Turkish/English prompt set;"
  echo "   the shipped prompts are all English, so prose may differ slightly.)"
  python3 "$SC/category-speed.py" "$API" | sed 's/^/  /' || FAILED=1
fi

# -------------------------------------------------------------- memory ------
if want memory; then
  hdr "free memory       expect at 0.88: head 4.6-4.7 GB free with ~450 MB swap,"
  echo "                                   workers 6.8-6.9 GB free"
  echo "  (below ~2 GiB available, or swap in the GB range, means back off one rung)"
  bash "$SC/free-memory-snapshot.sh" audit || FAILED=1
  for h in $NODES; do
    echo -n "  $h swap: "; $SSH "$h" "free -m | grep Swap | tr -s ' '"
  done
fi

echo
if [ "$FAILED" = "0" ]; then
  echo "=== audit finished. Read the numbers above against the expected ranges;"
  echo "    this script does not grade them for you."
else
  echo "=== audit finished WITH ERRORS (a check could not run). See above."
fi
exit "$FAILED"
