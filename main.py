import platform
import psutil
import json
import urllib.request
import subprocess

GB = 1024 ** 3
OLLAMA_URL = "http://127.0.0.1:11434"


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


if __name__ == "__main__":
    print("LocalAI Optimizer v0.4.0")
    scan_hardware()
    scan_models()
