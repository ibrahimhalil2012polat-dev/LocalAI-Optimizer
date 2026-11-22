# LocalAI Optimizer

An open-source, Windows-first toolkit for inspecting hardware, estimating local LLM memory requirements, and benchmarking Ollama on resource-constrained computers.

**Status: v0.7.0 Alpha.** Smart Auto Optimizer searches for a CPU profile for local Ollama on Windows. Apply remains a separate action for LocalAI Optimizer chat.

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

### Main menu

```powershell
.\.venv\Scripts\python.exe main.py
```

Choose **Smart Auto Optimizer**, select a local model, review the proposed candidates, and type `TEST`. A passing candidate appears under **Apply Optimization**, where you can preview it and type `APPLY`. **Chat** uses the active options, **Status** shows them, and **Restore Defaults** removes the active settings after confirmation.

The staged **Optimize** and **Quality Check** menus remain available for existing experimental profiles. For that workflow, run the Optimize stages in order with the same model. Generate code samples in **Detailed** mode, run the three validators, then create the quality summary.

### Smart Auto Optimizer

- Available CPU cores, the model's context limit and current Windows RAM and commit headroom determine the thread, batch and requested context candidates. The search is CPU only and currently supports local text completion GGUF models with supported Qwen2 or Llama memory metadata.
- Each eligible context tests up to six settings. It crosses up to three thread counts with batch 64 and a larger batch chosen from 128, 256 or 512 based on available memory. The reference uses Ollama's automatic thread selection and batch 512. Each round runs the same chat prompt, requested context, seed and output cap on the reference and all candidates. The reference moves from first to last to the middle over three rounds.
- A candidate must produce the same output and token counts as the reference across all rounds, stay within 20% timing spread for each setting, and spend at least 5% less time processing the prompt and generating the reply in every round. Cold load time is recorded separately. The report includes median times, per-round gains and rejection reasons. If a request stops because the memory check fails or the request times out, the report retains completed measurements and the run produces no recommendation.
- The highest qualifying context takes priority. Within that context, the largest median paired gain wins. That configuration then runs the same four Python and Unity C# tasks as its reference. Any regression, failed requirement, or uncertain result blocks a Smart profile. The console shows pass, fail and inconclusive counts for each setting and names unmet checks. C# checks use the local .NET SDK and Unity stubs. Generated samples are compiled or inspected without running them.
- The Smart profile records the full model SHA-256 digest, model and runner metadata, Ollama server version, Windows build, CPU identity and topology, available CPU affinity and installed RAM. Apply and Chat verify matching evidence. Preflight checks an idle Ollama, model limits, physical memory and Windows commit headroom before each Smart request. The client closes the request on critical memory pressure or timeout. An Ollama server can take time to release resources afterward.
- The menu shows the number of requests before asking for `TEST`. A full search can take tens of minutes, depending on the hardware and model. It uses a fixed short benchmark prompt, so it does not measure maximum length prompts or guarantee results on other workloads. Results and profiles stay in the local `results/` and `profiles/` directories. Smart profiles generated by the earlier two-round method need a new Smart test before Apply or Chat.

### Hardware inventory

```powershell
.\.venv\Scripts\python.exe main.py --analyze
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


## Experimental code-quality workflow (v0.5.0)

This optional workflow compares an **experimental fixed baseline** (Ollama default threads, batch 128, context 2048) with a **fixed test configuration** (8 threads, batch 256, context 2048). These values came from one CPU-only test machine; the quality scripts **do not yet load the optimal profile selected by** `combined_optimizer.py`. They are not a universal optimization recommendation. The combined 8192-context configuration has **not** undergone these code-quality checks.

Start Ollama with a downloaded model and run:

```powershell
.\.venv\Scripts\python.exe quality_engine.py
.\.venv\Scripts\python.exe code_quality_engine.py
```

`quality_engine.py` asks six exact-output programming questions. `code_quality_engine.py` generates two Python functions and two Unity C# scripts with both configurations, using CPU-only inference and preflight memory checks. Select detailed mode for all four generation tasks. Inference can consume significant RAM; cancellation is best effort.

Next, with a **.NET SDK** installed (`dotnet --version`), analyze the saved code-generation report. Ollama does not need to be running for these local checks:

```powershell
.\.venv\Scripts\python.exe compilation_validator.py
.\.venv\Scripts\python.exe behavior_validator.py
.\.venv\Scripts\python.exe unity_behavior_validator.py
.\.venv\Scripts\python.exe quality_summary.py
```

The compilation validator uses Python `compile(..., "exec")` for syntax checking **without executing the generated Python code**, and builds generated C# as a .NET class library against limited UnityEngine stubs. This is **not actual Unity compilation or a runtime test**. The Python behavior validator interprets a restricted subset of AST nodes against fixed test inputs instead of running generated code. The Unity behavior validator recognizes a few source-code patterns and can flag the specific error of assigning absolute 90-degree door rotation rather than adding 90 degrees to the initial local rotation; unrecognized alternatives receive `INCONCLUSIVE`. These limited checks can produce false positives or negatives.

`quality_summary.py` joins reports from the latest matching code-generation run into JSON and Markdown summaries in ignored `results/`. Its 12 per-task checks cover static structure, compilation and limited behavior; they are **not 12 independent generated programs**. A failed requirement is not cancelled out by finding no regression. `LIMITED_TESTS_PASSED` does not mean general model quality is proven; the summary keeps `automatic_apply_approved=false`. Generated source is retained inside local JSON reports: review and redact before sharing.

Run the included non-inference smoke tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Manual single-machine observation (Qwen2.5-Coder 7B):** baseline and fixed test configuration both answered 6/6 short output questions, passed 4/4 static checks and 4/4 Python-syntax/C#-stub compilation checks, and passed 14/14 restricted Python behavior cases. Both failed the same Unity door rotation requirement while the flashlight pattern was recognized. The combined quality summary showed **11/12 checks passed for each configuration**, with no newly detected regression, but status `QUALITY_REQUIREMENTS_FAILED`. These observations do not establish a general speedup, equivalent overall quality, or the correctness of code in Unity.

### Remaining quality limitations

- Tests are small and partly pattern-based; generated programs are not run in a sandbox or the Unity Editor.
- The baseline/optimized quality options are fixed and not propagated from per-user optimizer profiles.
- Compiling untrusted C# consumes local compiler resources; the controlled project avoids invoking the generated assembly, but this is **not a security sandbox**.
## Apply, chat and restore (v0.6.0)

Apply matches a combined profile and quality evidence to the exact installed model digest. The profile must match the machine and the tested CPU options. The chat context is capped at the context used in the quality test. Incomplete or inconclusive checks, optimized-only failures and mismatched settings block activation. Review the preview and type `APPLY` when all limited checks pass, or `I ACCEPT` to opt in with explicitly listed shared failures. A memory preflight must pass before saving the active profile.

Activation writes `profiles/active_optimization.json` locally. Chat passes the active settings to Ollama on localhost for each guarded request. It checks the model and report again before use and keeps a short history within the session. Restore Defaults asks for `RESTORE` and removes the active configuration. The local profile and report directories are ignored by Git.

The earlier Windows smoke test used `qwen2.5-coder:7b` at `num_thread=8`, `num_batch=256`, `num_ctx=2048` and `num_gpu=0`. The apply, status, chat (response `OK`) and restore steps completed. Both configurations passed 11 of 12 limited checks and failed `unity_door/behavior`. That result supports manual testing only. The smoke test measures connectivity and the profile lifecycle. Quality approval and performance gains require separate evidence.

`apply_optimization.py` also provides a standalone Apply, Chat, Restore and Status menu.

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
- Broader reproducible quality evaluation, GPU offloading analysis, dynamic cross-stage calibration, and automatic application remain planned. No speedup is guaranteed.
- The older benchmark and experimental optimization scripts duplicate safety logic. A shared tested monitor and stronger preflight checks remain future work. Cancellation is best effort. Prompt cache detection depends on API metadata availability.

## Development

See [ROADMAP.md](ROADMAP.md) and [CHANGELOG.md](CHANGELOG.md). Bug reports and reproducible test results are welcome. Remove personal identifiers from reports before sharing them.

## License

[MIT](LICENSE).
