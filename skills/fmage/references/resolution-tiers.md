# Resolution Tiers

- `resolution: "1k"`, `"2k"`, `"3k"`, or `"4k"` selects a target resolution tier. It does not promise that either edge will equal the tier number, and `4k` does not mean `4096x4096`.
- Fmage combines the tier with `aspect` and the selected transport's limits, then writes the normalized dimensions actually sent upstream to `requested_size` and `request.size`.
- Judge delivery against `requested_size`, not the tier label. If `image_metadata` matches `requested_size` and `warnings` contains no size warning, the provider returned exactly what Fmage requested.
- Example: an `openai-images` square `4k` tier can normalize to `2880x2880`; a 16:9 `4k` tier can normalize to `3840x2160`. Both are normal when request and output dimensions match.
- Report a mismatch only when an output's actual dimensions differ from `requested_size`, the plugin emits a size warning, or the user explicitly requested an exact `size` that was not delivered. Do not propose a retry solely to reach `4096x4096`.
