# Changelog

## 0.7.0 — Alpha (unreleased)

- Added Smart Auto Optimizer for bounded CPU thread, batch and context search on Windows.
- Added model digest, CPU and Ollama version matching, conservative memory preflight and guarded local requests.
- Added paired performance and code quality comparisons with matching prompts and contexts. Smart profiles require all limited quality checks to pass and explicit activation through Apply.
- Preserved the legacy Apply, Chat and Restore workflow.

## 0.6.0 — Alpha (unreleased)

- Added manual activation and restore of a local CPU profile for the guarded Ollama chat runner.
- Apply checks the model digest, hardware, preliminary validation and matching limited quality results. It uses the context size tested for quality and requires explicit acknowledgment of shared failures.
- Added a main menu for Analyze, Optimize, Quality Check, Apply, Chat, Restore and Status. The optimization and quality stages retain their individual prompts.
- Earlier Windows smoke test completed Apply → Status → Chat (`OK`) → Restore for `qwen2.5-coder:7b`. Both quality configurations passed 11/12 limited checks, with the same `unity_door/behavior` failure. No automatic quality approval or general speedup was established.

## 0.5.0 — Alpha (2026-11-22)

- Added CPU-only exact-output quality checks comparing baseline and fixed test settings.
- Added Python/Unity C# generation tasks, static checks, C# stub compilation, restricted Python behavior interpretation, and Unity source-pattern analysis.
- Added quality summary JSON/Markdown reports and six local smoke tests.
- Manual Qwen2.5-Coder 7B test: 11/12 limited quality checks passed in both configurations; both failed the same Unity door relative-rotation requirement. Status QUALITY_REQUIREMENTS_FAILED; no detected optimization-specific regression.
- This is **not** full code/Unity runtime validation, an end-to-end speedup guarantee, or authorization for automatic application. Settings are not persisted; published source has not been rerun end-to-end in a clean Windows environment.

## 0.4.0 — Alpha (2026-11-20)

- Added repeated thread and batch benchmarking with rotation of candidate order.
- Added quick and detailed context comparisons with context-specific preflight.
- Added selection of compatible local reports, a guarded final validation run and advisory optimization profiles.
- Added output consistency checks and documented observed single-machine results.
- The current batch and context defaults are fixed to the original test configuration; automatic cross-stage calibration, quality evaluation and persistent application are not included.
- Source formatting and version banners were normalized for publication. Published sources have not been independently rerun.


## 0.3.0 — Alpha (2026-11-20)

- Added interactive three-run profiling with the existing adaptive benchmark checks and model-digest verification.
- Added local profile statistics and history-based advisory memory estimation with conservative fallback.
- Documented preliminary three-run validation and limitations.
- Excluded local profiles from version control.
- Historical estimates do not override benchmark safety; automatic configuration optimization remains unreleased.

## 0.2.0 — Alpha (2026-11-20)

- Added CPU/GPU name detection.
- Added architecture-based CPU memory estimates and a read-only compatibility report.
- Added interactive CPU benchmark with adaptive physical memory preflight and Windows commit headroom checks.
- Added live memory-pressure sampling and experimental client-side interruption.
- Added local JSON metrics for throughput, first content latency, load time, RAM, CPU, commit headroom and disk activity.
- Documented manual validation, current limitations and future work.

The experimental automatic thread-search script is not included until its safety integration and repeatability have been validated.

## 0.1.0 — Alpha (2026-11-20)

- Initial hardware inventory and Ollama installed-model discovery.
- Setup instructions, MIT license and development roadmap.
