from pathlib import Path
p=Path("/usr/local/lib/python3.12/dist-packages/vllm/models/glm5next/nvidia/pooled_indexer.py"); s=p.read_text()
old="        if parent_stride_bytes % _INDEX_PAGE_BYTES:\n"
assert s.count(old)==1, s.count(old)
new=("        print(f\"[HAREM-C4-PROBE] shape={tuple(main_cache.shape)} stride={tuple(main_cache.stride())} "
     "block={block_size} need={block_size*(_MLA_RECORD_BYTES+33)} parent_stride_bytes={parent_stride_bytes} "
     "semantic={semantic_page_bytes} tail={index_tail_bytes} index_page_bytes={_INDEX_PAGE_BYTES} "
     "rem={parent_stride_bytes % _INDEX_PAGE_BYTES} extra={parent_stride_bytes - semantic_page_bytes - index_tail_bytes}\", flush=True)\n"+old)
p.write_text(s.replace(old,new,1)); print("probe inserted")
