"""HAREM-B12X-QPAD: run the b12x sparse-MLA decode/extend plans on an 8-aligned local
head count (TP=3 -> 22 real heads, 24 planned).  Query heads 22..23 are zero; the first
22 rows of O/LSE are copied back through pre-reserved workspace staging buffers (no
allocation at run time -> CUDA-graph safe).  Anchored edits, each count==1."""
import sys
p = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/lib/python3.12/dist-packages/vllm/v1/attention/backends/mla/b12x_mla_sparse.py"
s = open(p).read()
MARK = "HAREM-B12X-QPAD"
if MARK in s:
    print("already patched"); sys.exit(0)
def rep(old, new):
    global s
    assert s.count(old) == 1, f"anchor occurs {s.count(old)} times, expected 1: {old[:60]!r}"
    s = s.replace(old, new)
# 1) round the planned/workspace head count up to 8; keep the real head count
rep('''        self._input_num_heads = self.num_heads * self.dcp_world_size
        self._q_head_dim = self.kv_lora_rank + self.qk_rope_head_dim
''', '''        self._input_num_heads = self.num_heads * self.dcp_world_size
        # HAREM-B12X-QPAD: b12x MLA kernels are validated on 8-aligned head counts
        # (TP=2 -> 32, TP=4 -> 16); TP=3 leaves 22 (16 + 6 remainder).  Plan and run on
        # the next multiple of 8 with zero query heads; slice O/LSE back to 22.
        self._harem_real_heads = self._input_num_heads
        self._input_num_heads = ((self._input_num_heads + 7) // 8) * 8
        self._harem_qpad = self._input_num_heads != self._harem_real_heads
        self._q_head_dim = self.kv_lora_rank + self.qk_rope_head_dim
''')
# 2) staging-buffer specs (q, output, lse at the real head count) appended to BOTH spec lists
rep('''        q_spec = (
            (self._max_tokens, self._input_num_heads, self._q_head_dim),
            torch.bfloat16,
        )
        return (q_spec, *plan.shapes_and_dtypes())
''', '''        q_spec = (
            (self._max_tokens, self._input_num_heads, self._q_head_dim),
            torch.bfloat16,
        )
        return (q_spec, *plan.shapes_and_dtypes(), *self._harem_qpad_specs())

    def _harem_qpad_specs(self):
        # HAREM-B12X-QPAD staging: q (real heads), output (real heads), lse (real heads).
        if not self._harem_qpad:
            return ()
        real = self._harem_real_heads
        return (
            ((self._max_tokens, real, self._q_head_dim), torch.bfloat16),
            ((self._max_tokens, real, self.kv_lora_rank), torch.bfloat16),
            ((self._max_tokens, real), torch.float32),
        )
''')
rep('''        return (
            q_spec,
            *plan.shapes_and_dtypes(),
            *(ckv_specs if include_ckv else ()),
        )
''', '''        return (
            q_spec,
            *plan.shapes_and_dtypes(),
            *(ckv_specs if include_ckv else ()),
            *self._harem_qpad_specs(),  # HAREM-B12X-QPAD (always last)
        )
''')
# 3) run path: slice the workspace, zero-pad q
rep('''        workspaces = current_workspace_manager().get_simultaneous(*workspace_specs)
        q_buffer = workspaces[0]
        scratch_end = len(workspace_specs) - (2 if use_ckv_gather else 0)
        scratch = workspaces[1:scratch_end]

        if isinstance(q, tuple):
            q_nope, q_pe = q
            q_all = q_buffer[:num_tokens]
            if int(q_pe.shape[-1]) == 0:
                q_all.copy_(q_nope)
            else:
                ops.concat_mla_q(q_nope, q_pe, q_all)
        else:
            q_all = q_buffer[:num_tokens]
            q_all.copy_(q)
''', '''        workspaces = current_workspace_manager().get_simultaneous(*workspace_specs)
        q_buffer = workspaces[0]
        # HAREM-B12X-QPAD: staging buffers are the last three specs (when padding).
        n_qpad = 3 if (self._harem_qpad and not use_ckv_gather) else 0
        if self._harem_qpad and not use_ckv_gather:
            qpad_q, qpad_out, qpad_lse = workspaces[-3:]
        scratch_end = len(workspace_specs) - (2 if use_ckv_gather else 0) - n_qpad
        scratch = workspaces[1:scratch_end]

        if n_qpad:
            real = self._harem_real_heads
            q_all = q_buffer[:num_tokens]
            q_stage = qpad_q[:num_tokens]
            if isinstance(q, tuple):
                q_nope, q_pe = q
                if int(q_pe.shape[-1]) == 0:
                    q_stage.copy_(q_nope)
                else:
                    ops.concat_mla_q(q_nope, q_pe, q_stage)
            else:
                q_stage.copy_(q)
            q_all[:, :real].copy_(q_stage)
            q_all[:, real:].zero_()
        elif isinstance(q, tuple):
            q_nope, q_pe = q
            q_all = q_buffer[:num_tokens]
            if int(q_pe.shape[-1]) == 0:
                q_all.copy_(q_nope)
            else:
                ops.concat_mla_q(q_nope, q_pe, q_all)
        else:
            q_all = q_buffer[:num_tokens]
            q_all.copy_(q)
''')
# 4) result: copy the first real-head rows into the staging buffers and return those
rep('''        result = run(**run_kwargs)
        if self.need_to_return_lse_for_decode:
            output, lse = result
            return output, lse
        assert isinstance(result, torch.Tensor)
        return result, None
''', '''        result = run(**run_kwargs)
        if n_qpad:  # HAREM-B12X-QPAD: slice the padded heads away (no allocation)
            real = self._harem_real_heads
            if self.need_to_return_lse_for_decode:
                output, lse = result
                out_s = qpad_out[:num_tokens]
                out_s.copy_(output[:, :real, : out_s.shape[-1]])
                lse_s = qpad_lse[:num_tokens]
                lse_s.copy_(lse[:, :real])
                return out_s, lse_s
            assert isinstance(result, torch.Tensor)
            out_s = qpad_out[:num_tokens]
            out_s.copy_(result[:, :real, : out_s.shape[-1]])
            return out_s, None
        if self.need_to_return_lse_for_decode:
            output, lse = result
            return output, lse
        assert isinstance(result, torch.Tensor)
        return result, None
''')
open(p, "w").write(s)
import py_compile; py_compile.compile(p, doraise=True)
print(f"patched: {p} marker={s.count(MARK)}")
