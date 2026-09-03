# fix-A — drafter gets its own load format (permanent replacement for INSTANTTENSOR_COPY=true)

Root cause (confirmed by the 00:18 run): the drafter inherits the target's
`--load-format instanttensor` + `{"instanttensor_copy": false}`, and `copy=false`
hands out borrowed ring-buffer views. Fix: give the drafter its own LoadConfig;
target keeps instanttensor + copy=false.

## 0. Key-name verification (all read-only, this fork)
- `draft_load_config` IS a real SpeculativeConfig field — `vllm/config/speculative.py:515-517`
  ("Load config for the draft model. If not specified, will use the load config from the
  target model"). Consumed at `vllm/v1/worker/gpu/spec_decode/dflash/utils.py:51`.
- Fallback proven: `vllm/model_executor/model_loader/__init__.py:134`
  `get_model_loader(load_config or vllm_config.load_config)`.
- Nested dict IS coerced: `--speculative-config` is `json.loads`'d
  (`vllm/engine/arg_utils.py:1645`) then splatted `SpeculativeConfig(**self.speculative_config)`
  (`:1908`); `SpeculativeConfig` is `@config` = pydantic dataclass with `extra="forbid"`
  (`vllm/config/utils.py:51-72`), and `LoadConfig` is `@config` too (`vllm/config/load.py:26-27`),
  so `{"load_format": "..."}` becomes a LoadConfig. A typo'd key is REJECTED, not ignored.
- `safetensors` IS a legal load_format: `vllm/model_executor/model_loader/__init__.py:60`
  (`"safetensors": DefaultModelLoader`); `vllm/config/load.py:30` + lowercasing validator `:135`.
- No extra-config collision: `LoadConfig.model_loader_extra_config` is
  `Field(default_factory=dict)` (`vllm/config/load.py:93`), so the drafter's fresh LoadConfig
  carries `{}` and the guard at `vllm/model_executor/model_loader/default_loader.py:137-141`
  ("instanttensor_copy with a non-instanttensor load_format") CANNOT fire.

## 1. Edit to ~/glm3x/scripts/start-lil.sh
Add one variable next to the other DFlash knobs (near DFLASH_DRAFT_TP):

    DRAFT_LOAD_FORMAT="${DRAFT_LOAD_FORMAT:-safetensors}"

Then replace the single SPEC_ARG assignment. BEFORE (one line, count==1):

    SPEC_ARG=(--speculative-config "{\"method\":\"dflash\",\"model\":\"${DRAFT_PATH}\",\"num_speculative_tokens\":${DFLASH_NUM_SPEC},\"kv_cache_dtype\":\"auto\",\"draft_tensor_parallel_size\":${DFLASH_DRAFT_TP}}")

AFTER:

    SPEC_ARG=(--speculative-config "{\"method\":\"dflash\",\"model\":\"${DRAFT_PATH}\",\"num_speculative_tokens\":${DFLASH_NUM_SPEC},\"kv_cache_dtype\":\"auto\",\"draft_tensor_parallel_size\":${DFLASH_DRAFT_TP},\"draft_load_config\":{\"load_format\":\"${DRAFT_LOAD_FORMAT}\"}}")

Nothing else changes. The target's `--load-format instanttensor` and
`--model-loader-extra-config '{"instanttensor_copy":false}'` stay exactly as they are.

## 2. Env lines for ~/glm3x/.env.lil-t3
Add:

    DRAFT_LOAD_FORMAT=safetensors      # drafter off instanttensor; target keeps it
    INSTANTTENSOR_COPY=false           # target only; borrowed views are safe there

NOTE: `INSTANTTENSOR_COPY` is NOT read by start-lil.sh as shipped — the target's copy flag is
hard-coded in the `--model-loader-extra-config` literal. Either keep the literal `false` and
treat the env line as documentation, or make it live with the same one-line style:
`--model-loader-extra-config "{\"instanttensor_copy\":${INSTANTTENSOR_COPY:-false}}"`.
Say which you want; I did not change it.

## 3. One-line verification
    docker logs harem_glm53_lil 2>&1 | grep -E "Loading safetensors (checkpoint shards|using InstantTensor loader)|Loading weights took"

PASS looks like: first the target's `Loading safetensors using InstantTensor loader` +
`Loading weights took <N> seconds`, then for the drafter `Loading safetensors checkpoint shards`
+ a second `Loading weights took`. There must be NO second "using InstantTensor loader" line and
no second "Loading NN tensors larger than the ... InstantTensor buffer" line.
Desc strings: plain path `weight_utils.py:906`, instanttensor path `weight_utils.py:1287` and `:1337`.

## 4. INSTANTTENSOR_BUFFER_SIZE — measured, not guessed
Rule: `use_cpu_fallback = tensor_size > INSTANTTENSOR_BUFFER_SIZE`
(`weight_utils.py:1463-1468`); the buffer is then auto-enlarged to the largest tensor
(`instanttensor/_impl.py:_determine_buffer_size`).
Measured from the safetensors headers (current 64 MiB = 67108864 setting):

| checkpoint | tensors | >64 MiB | >128 MiB | >256 MiB | largest |
|---|---|---|---|---|---|
| target  | 148,498 | 26 (log agrees) | 2 | 2 | 1,268,776,960 B (1210 MiB) `model.language_model.embed_tokens.weight` |
| drafter | 81      | 18 (log agrees) | 1 | 0 | 167,772,160 B (160 MiB) `fc.weight` |

- 256 MiB would cut the TARGET's CPU fallbacks 26 -> 2, but NOT eliminate the branch: the two
  1210 MiB tensors (embed_tokens, lm_head) would still fall back. Eliminating it entirely needs
  >= 1,268,776,960 B, i.e. a 1.2 GB pinned staging buffer at gpu-memory-utilization 0.85 — do not.
- 256 MiB WOULD eliminate it for the drafter (largest 160 MiB) — but with fix-A the drafter no
  longer touches instanttensor, so that is moot.
- Recommendation: leave INSTANTTENSOR_BUFFER_SIZE at 67108864. Raising it to 256 MiB is a
  target-only load-speed experiment with unmeasured benefit; it is not part of this fix. # NOT MEASURED
