# Development roadmap

## v0.1.0 — Hardware and model inventory

- [x] CPU and RAM inventory
- [x] Local Ollama connectivity and model listing

## v0.2.0 — Compatibility and adaptive monitoring

- [x] CPU and GPU name detection
- [x] Qwen2/Llama architecture-based F16 KV-cache estimates
- [x] CPU-only compatibility analysis
- [x] Adaptive preflight and explicit benchmark confirmation
- [x] Sampled physical memory, Windows commit headroom, CPU and disk activity
- [x] Token throughput, latency and local JSON benchmark reports
- [ ] Mocked automated tests and validation of cancellation behavior

## Next — Reliable measurement

- [ ] Use a single shared memory policy across compatibility and benchmarking
- [ ] Repeat tests under comparable conditions and report variance
- [ ] Learn per-model, per-hardware memory profiles from observed measurements
- [ ] Separate paging from aggregate disk activity
- [ ] Improve commit requirement estimation and server-side cancellation verification

## Later — Optimization and quality

- [ ] Guarded thread, context, batch and GPU offload comparisons
- [ ] Reproducible output quality tests
- [ ] Recommend configurations based on speed, quality and memory trade-offs
- [ ] Evaluate quantization and KV-cache types
- [ ] Optional graphical interface, additional backends and opt-in community benchmarks

No version dates or speedups are promised.
