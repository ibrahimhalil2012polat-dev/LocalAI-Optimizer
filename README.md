# LocalAI Optimizer

An open-source, Windows-first toolkit for inspecting hardware, estimating local LLM memory requirements, and benchmarking Ollama on resource-constrained computers.

**Status: v0.3.0 Alpha — repeated benchmarks and history-based memory estimates.** Automatic configuration optimization is not yet part of this release.

## Features

- CPU and GPU name detection, CPU core counts and system RAM inventory.
- Local Ollama model discovery, on-disk sizes, and quantization metadata.
- Preliminary CPU-only memory estimates for Qwen2 and Llama architectures at 1K, 2K, 4K and 8K context lengths.
- F16 KV-cache estimation using model architecture metadata.
- Adaptive preflight checks for available physical RAM, loaded models, and Windows commit headroom when available.
- CPU-only inference benchmark with explicit user confirmation.
- Token throughput, first streamed content latency (TTFT approximation), model load time, total time, and sampled system RAM, CPU, commit headroom and disk I/O.
- Best-effort interruption when monitored memory becomes critical.
- Local JSON benchmark reports.
- Three-run model profiling with mean, median, variability and memory statistics.
- Read-only historical memory estimates matched by model name, digest, hardware signature and benchmark options.

## Requirements

- Windows (initial test platform).
- Python 3.12 (initial target).
- Ollama running locally, with a supported downloaded model.
- psutil (see requirements.txt).

## Installation

```powershell
git clone https://github.com/ibrahimhalil2012polat-dev/LocalAI-Optimizer.git
cd LocalAI-Optimizer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For uv-managed Python installations:

```powershell
uv venv --python 3.12
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

## Usage

Launch Ollama, or run `ollama serve` in a separate terminal if it is not running.

### Hardware inventory

```powershell
.\.venv\Scripts\python.exe main.py
```

### Read-only compatibility analysis

```powershell
.\.venv\Scripts\python.exe compatibility.py
```

The inventory estimates model memory from stored model size, a heuristic runtime overhead, and an F16 KV-cache calculation. It uses a conservative inventory reserve that differs from the adaptive benchmark preflight policy; their eligibility results may differ.

### Adaptive benchmark

```powershell
.\.venv\Scripts\python.exe benchmark.py
```

Select a local model. If the preflight allows a controlled test, confirm it with `y`. The preflight repeats immediately before the request. The benchmark requests CPU-only inference, 2048 context, a maximum of 128 generated tokens, and batch size 128. Existing loaded models are not automatically unloaded. The benchmark requests unloading its tested model after the call.

Reports are written locally to `results/`, which is excluded from version control. No telemetry or model downloads are performed by this toolkit. Its Ollama API requests target localhost.

### Repeated benchmarks and local model profiles

```powershell
.\.venv\Scripts\python.exe profiler.py
```

Select a model and explicitly confirm the three-run sequence. Each run invokes the existing adaptive benchmark, waits for previously loaded models to unload, and checks for a changed model digest. The sequence stops on an unsuccessful run. The profiler saves a local summary and measurements to `profiles/`; this directory is excluded from Git.

### History-based memory analysis

```powershell
.\.venv\Scripts\python.exe memory_engine.py
```

The engine reads compatible, completed historical measurements and requires at least three unique full-length runs before reporting a learned estimate. It calculates the observed maximum plus a heuristic allowance (the larger of 0.5 GiB or 15% of that maximum). The displayed effective estimate is the maximum of the theoretical and historical estimates. This is a read-only advisory report, not a replacement for benchmark preflight, and never relaxes the existing safety policy. No matching history means the theoretical estimate is used.

## Initial manual validation

A Windows test system with approximately 15.74 GiB RAM and Ollama 0.40.15 reported one successful Qwen2.5-Coder 7B benchmark:

| Metric | Observed value |
| --- | --- |
| Generated tokens | 128 |
| Generation throughput | 7.094 tokens/s |
| First streamed content | 11.891 s |
| Load time | 10.580 s |
| Total time | 29.935 s |
| Minimum available system RAM | 3.232 GiB |
| Observed decrease in available system RAM | 4.412 GiB |
| Minimum Windows commit headroom | 3.046 GiB |
| Peak sampled system CPU usage | 75.6% |

The read-only estimator was also exercised with 7B and 14B Qwen2 models. An earlier conservative preflight blocked a 14B test before inference. The adaptive preflight allowed the successful 7B run. These are manual single-system observations, not performance guarantees or controlled comparisons.

### v0.3.0 repeated-run validation

Three successful Qwen2.5-Coder 7B runs on the same Windows system, with identical benchmark options, produced:

| Metric | Observed value |
| --- | --- |
| Throughput (individual runs) | 7.032, 6.993, 7.279 tokens/s |
| Mean throughput | 7.101 tokens/s |
| Median throughput | 7.032 tokens/s |
| Throughput coefficient of variation | 1.783% |
| Mean decrease in available system RAM | 4.595 GiB |
| Maximum decrease in available system RAM | 4.661 GiB |
| Median first streamed content latency | 11.850 s |
| Historical memory estimate including allowance | 5.360 GiB |
| Theoretical memory estimate | 5.471 GiB |

The memory engine found all three matching measurements and retained the more conservative 5.471 GiB effective estimate. These are preliminary, single-machine observations and not evidence of a general speedup or exact model-only memory usage.

## Limitations and responsible use

- Alpha software. Memory estimates are approximate; eligibility does not guarantee that inference will fit or avoid system instability.
- Runtime overhead and adaptive reserve values are heuristic. Memory can change between samples, and sampled values may miss short-lived peaks.
- The compatibility estimator assumes one sequence and F16 KV cache. GPU memory, nonstandard cache layouts, concurrency, and other architectures are not modeled.
- Windows commit headroom is checked when the API succeeds; this is not a complete prediction of the model's commit requirements.
- RAM and CPU measurements are system-wide. Model disk size is not its measured runtime RAM usage.
- Disk activity is aggregate system I/O, not a measure of paging alone.
- Cancellation closes the client connection on a best-effort basis. Instant server-side cancellation and model unloading are not guaranteed. The critical-pressure cancellation path has not yet been validated on real hardware; do not deliberately exhaust memory to test it.
- Three-run profiles do not eliminate background load, thermal effects, OS file caching or other sources of variation. The model is unloaded between rounds, but OS caches are not cleared.
- Historical profiles do not yet capture Ollama version or a complete hardware/runtime fingerprint. Historical estimates are heuristic and advisory; they have no authority to override benchmark preflight.
- Quality evaluation, GPU offloading analysis and automatic setting selection remain planned. No speedup is guaranteed.

## Development

See [ROADMAP.md](ROADMAP.md) and [CHANGELOG.md](CHANGELOG.md). Bug reports and reproducible test results are welcome. Remove personal identifiers from reports before sharing them.

## License

[MIT](LICENSE).
