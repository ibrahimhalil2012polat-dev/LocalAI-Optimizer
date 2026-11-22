import argparse
import platform
import psutil
import json
import sys
import urllib.request
import subprocess
from pathlib import Path

import apply_optimization

GB = 1024 ** 3
OLLAMA_URL = "http://127.0.0.1:11434"
ROOT = Path(__file__).resolve().parent


def powershell(command):
    try:
        return subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", command],
            timeout=15,
            text=True
        ).strip()
    except Exception:
        return "Unknown"


def scan_hardware():
    ram = psutil.virtual_memory()

    cpu = powershell("(Get-CimInstance Win32_Processor).Name")
    gpu = powershell("(Get-CimInstance Win32_VideoController).Name")

    print("\n=== HARDWARE ANALYSIS ===")
    print("OS:", platform.platform())
    print("CPU:", cpu)
    print("GPU:", gpu)
    print("Physical cores:", psutil.cpu_count(logical=False))
    print("Logical cores:", psutil.cpu_count(logical=True))
    print("CPU usage:", psutil.cpu_percent(interval=1), "%")
    print(f"Total RAM: {ram.total / GB:.2f} GiB")
    print(f"Available RAM: {ram.available / GB:.2f} GiB")
    print(f"RAM usage: {ram.percent}%")


def scan_models():
    print("\n=== OLLAMA MODELS ===")
    try:
        with urllib.request.urlopen(
            OLLAMA_URL + "/api/tags", timeout=5
        ) as response:
            data = json.load(response)

        models = data.get("models", [])
        print("Ollama: Connected")
        print("Installed models:", len(models))

        for model in models:
            name = model["name"]
            size = model["size"] / GB
            quant = model.get("details", {}).get(
                "quantization_level", "Unknown"
            )
            print(f"\nModel: {name}")
            print(f"Size: {size:.2f} GiB")
            print(f"Quantization: {quant}")

        return models

    except Exception as error:
        print("Ollama connection failed:", error)
        return []


def analyze():
    scan_hardware()
    scan_models()


def run_script(script):
    # Isolate each legacy tool's globals and keep its interactive prompts.
    result = subprocess.run([sys.executable, str(ROOT / script)], cwd=ROOT)
    if result.returncode:
        print(f"{script} exited with status {result.returncode}.")


def choose(title, labels, exit_label):
    print(f"\n=== {title} ===")
    for number, label in enumerate(labels, 1):
        print(f"{number}. {label}")
    print(f"0. {exit_label}")
    choice = input("\nSelect: ").strip()
    if choice == "0":
        return None
    if choice in {str(number) for number in range(1, len(labels) + 1)}:
        return int(choice) - 1
    raise ValueError("Invalid selection")


def tools_menu(title, stages):
    labels = [label for label, _ in stages]
    while True:
        try:
            choice = choose(title, labels, "Back")
            if choice is None:
                return
            run_script(stages[choice][1])
        except KeyboardInterrupt:
            print("\nCancelled")
        except (OSError, ValueError) as exc:
            print("ERROR:", exc)


def optimize():
    tools_menu("OPTIMIZE", [
        ("Compare threads", "optimizer.py"),
        ("Compare batches", "batch_optimizer.py"),
        ("Compare context sizes", "context_optimizer.py"),
        ("Build combined profile", "combined_optimizer.py"),
    ])


def quality_check():
    tools_menu("QUALITY CHECK", [
        ("Generate code samples (choose Detailed for all four tasks)", "code_quality_engine.py"),
        ("Check compilation", "compilation_validator.py"),
        ("Check Python behavior", "behavior_validator.py"),
        ("Check Unity source patterns", "unity_behavior_validator.py"),
        ("Create quality summary", "quality_summary.py"),
        ("Run optional short answer checks", "quality_engine.py"),
    ])


def main():
    parser = argparse.ArgumentParser(description="LocalAI Optimizer v0.6.0")
    parser.add_argument("--analyze", action="store_true", help="Print hardware and model inventory")
    if parser.parse_args().analyze:
        analyze()
        return

    actions = [
        ("Analyze", analyze),
        ("Optimize", optimize),
        ("Quality Check", quality_check),
        ("Apply Optimization", apply_optimization.apply),
        ("Chat", apply_optimization.chat),
        ("Restore Defaults", apply_optimization.restore),
        ("Status", apply_optimization.status),
    ]
    labels = [label for label, _ in actions]
    while True:
        try:
            choice = choose("LocalAI Optimizer v0.6.0", labels, "Exit")
            if choice is None:
                return
            actions[choice][1]()
        except EOFError:
            return
        except KeyboardInterrupt:
            print("\nCancelled")
        except RuntimeError as exc:
            print("BLOCKED:", exc)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print("ERROR:", exc)


if __name__ == "__main__":
    main()
