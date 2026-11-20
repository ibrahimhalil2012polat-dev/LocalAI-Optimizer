# LocalAI Optimizer

An open-source toolkit for inspecting, benchmarking and eventually optimizing local AI workloads on resource-constrained computers.

**Status: v0.1.0 — Alpha.** This initial version is a read-only hardware and model inventory tool. Benchmarking and automatic optimization are planned, not implemented.

## Features

- Operating system and processor identification
- Physical and logical CPU core counts and CPU utilization
- Total and available RAM and memory utilization
- Local Ollama connectivity check
- Installed model names, on-disk sizes and quantization levels

## Requirements

- Python 3.12 (initial target)
- Windows (initial test platform)
- Ollama installed and running locally for model inventory
- An installed Ollama model, if you want a nonempty inventory

## Installation

Clone or download the repository, then open PowerShell inside its folder.

```powershell
git clone https://github.com/ibrahimhalil2012polat-dev/LocalAI-Optimizer.git
cd LocalAI-Optimizer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If your Python installation is managed by uv, you can instead run:

```powershell
uv venv --python 3.12
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

## Usage

If Ollama is not running, start it in a separate terminal with `ollama serve`, or launch the Ollama application.

```powershell
.\.venv\Scripts\python.exe main.py
```

Administrator privileges are not required. The hardware report still works if Ollama is unavailable.

The tool queries the local Ollama API at `http://127.0.0.1:11434/api/tags`. It does not send reports to an external service, download or load models, or modify model settings. Model sizes represent stored files, not their actual runtime RAM or VRAM consumption. Memory and file sizes are displayed in GiB.

## Validation and limitations

The initial hardware inventory and Ollama model listing were manually tested on Windows. Other operating systems and hardware configurations are not yet validated. The processor identifier may not include the full commercial CPU name. No GPU detection, inference benchmark, or automatic optimization is included in this version.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned work.

## Contributing

Bug reports, reproducible test results and small pull requests are welcome. Include your OS, Python version and relevant error messages in reports, removing personal information before sharing.

## License

[MIT](LICENSE).
