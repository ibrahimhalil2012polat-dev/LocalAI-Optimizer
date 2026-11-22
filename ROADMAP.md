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

## v0.3.0 — Repeated benchmarks and historical memory profiles

- [x] Three-run benchmark sequence using the existing safety preflight
- [x] Throughput statistics, variability and local model profiles
- [x] Read-only history-based memory estimate with conservative allowance
- [x] Preliminary validation on one Windows system

## v0.4.0 — Experimental staged optimizer

- [x] Thread and batch comparisons (three runs per candidate)
- [x] Quick and detailed context comparison modes
- [x] Historical report selection and final validation
- [x] Local advisory optimization profiles
- [x] Initial manual workflow validation on one Windows system
- [ ] End-to-end default-versus-selected comparison on identical workloads
- [ ] Dynamic thread and batch propagation across calibration stages
- [ ] Support partial candidate sets, full hardware/runtime matching and default-thread winners

## v0.5.0 — Experimental quality verification

- [x] Exact-output code questions with baseline/fixed-configuration comparisons
- [x] Python and Unity C# code-generation structure checks
- [x] Python syntax and C# .NET stub compilation checks
- [x] Restricted Python behavior checks, without running generated code
- [x] Basic Unity C# behavior-pattern checks and requirement-failure reports
- [x] Local quality summaries and validator smoke tests
- [ ] True isolated runtime evaluation of untrusted code
- [ ] Unity Editor compilation and behavior testing
- [ ] Profile-driven quality comparisons across different hardware/configurations
- [ ] Apply/restore settings with an explicit quality gate and opt-in

## Next — Reliable optimization

- [ ] Use a single shared memory policy across compatibility and benchmarking
- [ ] Record Ollama version and more complete runtime fingerprints
- [ ] Validate memory estimates on additional hardware and workloads
- [ ] Separate paging from aggregate disk activity
- [ ] Improve commit requirement estimation and server-side cancellation verification

## Later — Optimization and quality

- [ ] Extend guarded CPU comparisons to GPU offload
- [ ] Reproducible output quality tests
- [ ] Recommend configurations based on speed, quality and memory trade-offs
- [ ] Evaluate quantization and KV-cache types
- [ ] Optional graphical interface, additional backends and opt-in community benchmarks

No version dates or speedups are promised.
