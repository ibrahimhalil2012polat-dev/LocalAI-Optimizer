# Changelog

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
