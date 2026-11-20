# LocalAI Optimizer

An open-source, Windows-first toolkit for inspecting hardware, estimating local LLM memory requirements, and benchmarking Ollama on resource-constrained computers.

**Status: v0.4.0 Alpha — staged CPU configuration benchmarking and preliminary optimization profiles.** The optimizer recommends settings; it does not automatically apply them.

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

- Thread, batch and context benchmarking with guarded inference.
- Combined profile selection from compatible local reports and a final validation run.

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

### Experimental CPU optimization workflow

Run the following scripts in order, selecting the same installed model each time:

```powershell
.\.venv\Scripts\python.exe optimizer.py
.\.venv\Scripts\python.exe batch_optimizer.py
.\.venv\Scripts\python.exe context_optimizer.py
.\.venv\Scripts\python.exe combined_optimizer.py
```

1. Thread search tests the default, half the physical core count, the physical core count and the logical core count (deduplicated).
2. Batch search measures prompt processing with batch sizes 64, 128, 256 and 512. The current prototype uses eight threads.
3. Context search tests 1024, 2048, 4096 and 8192 tokens using eight threads and batch 256. Quick mode uses one run per candidate; detailed mode uses three. Configurations that fail the memory check are skipped.
4. The combined optimizer selects the fastest observed median thread setting and the lowest observed RAM increase among batches within 1% of the fastest prompt throughput. The user selects a tested context. A new guarded test validates the selected configuration before an advisory profile is saved locally to `profiles/`.

**Current constraint:** the batch and context scripts use fixed settings from the original test machine. If the selected thread or batch differs, the combined optimizer will reject incompatible reports. The staged workflow is not yet a general automatic search for arbitrary hardware. A default-thread winner or a partial context comparison is not supported by the combined selector. Reports are matched by name, digest and applicable options, not a complete hardware/runtime fingerprint.

The scripts request CPU-only inference, unload after each call, use fixed prompts and compare output hashes. Larger batch reserve estimates are heuristic. Output hashes are a consistency check, not a quality test. All recommendations are preliminary; no persistent model settings are modified.

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

### v0.4.0 manual optimization observations

On the same Windows test machine, Qwen2.5-Coder 7B showed the following median generation throughput and maximum sampled decrease in available system RAM over three thread runs per candidate:

| Threads | Generation (tokens/s) | RAM (GiB) |
| --- | ---: | ---: |
| Default | 6.703 | 4.930 |
| 4 | 5.932 | 5.074 |
| 8 | 7.194 | 4.753 |
| 12 | 6.756 | 4.663 |

Eight threads were 7.33% faster than the default in this specific comparison. All compared default and winning-thread output hashes matched.

Batch search (three runs each, eight threads):

| Batch | Median prompt tokens/s | Median generation tokens/s | RAM (GiB) |
| --- | ---: | ---: | ---: |
| 64 | 26.625 | 6.973 | 4.791 |
| 128 | 26.903 | 7.071 | 5.186 |
| 256 | 27.317 | 6.992 | 4.654 |
| 512 | 27.381 | 7.071 | 4.807 |

Batch 512 was 1.78% faster than batch 128 for prompt processing. Batch 256 was selected as a balanced candidate; its tiny throughput difference from 512 may be measurement noise.

Context quick comparison (one run per context, eight threads, batch 256):

| Context | Prompt tokens/s | Generation tokens/s | RAM (GiB) |
| --- | ---: | ---: | ---: |
| 1024 | 30.518 | 7.592 | 4.428 |
| 2048 | 30.957 | 7.582 | 4.539 |
| 4096 | 30.636 | 7.475 | 4.556 |
| 8192 | 30.442 | 7.558 | 4.663 |

An earlier context run skipped three candidates due to insufficient estimated RAM. The subsequent complete comparison was conducted with more available RAM. This short-prompt benchmark does not establish full context capacity or long-input quality.

Combined validation: eight threads, batch 256, context 8192; prompt throughput 28.864 tokens/s, generation throughput 6.738 tokens/s, observed RAM increase 5.021 GiB, and minimum available system RAM 3.347 GiB. The validation output hash matched the corresponding context benchmark and the profile was marked PRELIMINARY_VALIDATED. A general combined speedup was not established; the validation used a different workload from the thread benchmark.

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
- Full quality evaluation, GPU offloading analysis, dynamic cross-stage calibration, and automatic application remain planned. No speedup is guaranteed.
- The older benchmark and experimental optimization scripts duplicate safety logic. A shared tested monitor and stronger preflight checks remain future work. Cancellation is best effort. Prompt cache detection depends on API metadata availability.

## Development

See [ROADMAP.md](ROADMAP.md) and [CHANGELOG.md](CHANGELOG.md). Bug reports and reproducible test results are welcome. Remove personal identifiers from reports before sharing them.

## License

[MIT](LICENSE).
