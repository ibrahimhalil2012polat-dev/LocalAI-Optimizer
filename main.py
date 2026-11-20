import os
import platform
import psutil
import json
import urllib.request

GB = 1024 ** 3

def scan_hardware():
    ram = psutil.virtual_memory()

    print("\n=== HARDWARE ANALYSIS ===")
    print("OS:", platform.platform())
    print("CPU:", os.environ.get(
        "PROCESSOR_IDENTIFIER", platform.processor()
    ))
    print("Physical cores:", psutil.cpu_count(logical=False))
    print("Logical cores:", psutil.cpu_count(logical=True))
    print("CPU usage:", psutil.cpu_percent(interval=1), "%")
    print(f"Total RAM: {ram.total / GB:.2f} GiB")
    print(f"Available RAM: {ram.available / GB:.2f} GiB")
    print(f"RAM usage: {ram.percent}%")

def scan_models():
    print("\n=== OLLAMA MODELS ===")

    try:
        url = "http://127.0.0.1:11434/api/tags"

        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.load(response)

        models = data.get("models", [])
        print("Ollama: Connected")
        print("Installed models:", len(models))

        for model in models:
            name = model["name"]
            size = model["size"] / GB
            details = model.get("details", {})
            quant = details.get("quantization_level", "Unknown")

            print(f"\nModel: {name}")
            print(f"Size: {size:.2f} GiB")
            print(f"Quantization: {quant}")

    except Exception as error:
        print("Ollama connection failed:", error)

if __name__ == "__main__":
    print("LocalAI Optimizer v0.1.0")
    scan_hardware()
    scan_models()
