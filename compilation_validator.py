
import json
import os
import re
import shutil
import subprocess
import tempfile

from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

UNITY_STUBS = """
using System;

namespace UnityEngine
{
    public class Object {}

    public class Component : Object
    {
        public Transform transform => new Transform();

        public T GetComponent<T>() where T : new()
        {
            return new T();
        }
    }

    public class MonoBehaviour : Component {}

    public class Transform : Component
    {
        public Quaternion localRotation { get; set; }
        public Quaternion rotation { get; set; }
        public Vector3 localEulerAngles { get; set; }
    }

    public class Light : Component
    {
        public bool enabled { get; set; }
    }

    public struct Vector3
    {
        public float x, y, z;

        public static Vector3 up => default;
    }

    public struct Quaternion
    {
        public static Quaternion identity => default;

        public static Quaternion Euler(
            float x, float y, float z
        ) => default;

        public static Quaternion Euler(
            Vector3 angles
        ) => default;

        public static Quaternion AngleAxis(
            float angle, Vector3 axis
        ) => default;

        public static Quaternion operator *(
            Quaternion a, Quaternion b
        ) => default;

        public static bool operator ==(
            Quaternion a, Quaternion b
        ) => false;

        public static bool operator !=(
            Quaternion a, Quaternion b
        ) => true;

        public override bool Equals(object obj)
            => obj is Quaternion;

        public override int GetHashCode() => 0;
    }

    public enum KeyCode
    {
        F
    }

    public static class Input
    {
        public static bool GetKeyDown(KeyCode key)
            => false;
    }

    public class SerializeField : Attribute {}

    public static class Debug
    {
        public static void Log(object message) {}
    }
}
"""


def get_dotnet_framework():
    if not shutil.which("dotnet"):
        raise RuntimeError(".NET SDK command not found.")

    process = subprocess.run(
        ["dotnet", "--version"],
        capture_output=True,
        text=True,
        timeout=15
    )

    if process.returncode != 0:
        raise RuntimeError(
            "Cannot read .NET SDK version."
        )

    version = process.stdout.strip()
    match = re.match(r"^(\d+)\.", version)

    if not match:
        raise RuntimeError(
            "Unrecognized .NET SDK version."
        )

    major = int(match.group(1))

    print(".NET SDK:", version)

    return f"net{major}.0"


def run_command(arguments, directory):
    environment = os.environ.copy()

    environment["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    environment["DOTNET_NOLOGO"] = "1"
    environment["DOTNET_SKIP_FIRST_TIME_EXPERIENCE"] = "1"
    environment["DOTNET_CLI_DO_NOT_USE_MSBUILD_SERVER"] = "1"

    try:
        return subprocess.run(
            arguments,
            cwd=directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            env=environment
        )
    except subprocess.TimeoutExpired:
        return None


def error_lines(process):
    if process is None:
        return ["Compilation timed out."]

    output = process.stdout + "\n" + process.stderr

    errors = [
        line.strip()
        for line in output.splitlines()
        if re.search(
            r"\berror\s+(?:CS|NETSDK|NU)\d+",
            line,
            re.IGNORECASE
        )
    ]

    if errors:
        return errors[:8]

    return output.strip().splitlines()[-8:]


def compile_csharp(source, framework):
    with tempfile.TemporaryDirectory(
        prefix="localai_compile_"
    ) as temp:
        folder = Path(temp)

        (folder / "Generated.cs").write_text(
            source,
            encoding="utf-8"
        )

        (folder / "UnityStubs.cs").write_text(
            UNITY_STUBS,
            encoding="utf-8"
        )

        project = f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>{framework}</TargetFramework>
    <OutputType>Library</OutputType>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <ImplicitUsings>disable</ImplicitUsings>
    <Nullable>disable</Nullable>
    <NuGetAudit>false</NuGetAudit>
    <RunAnalyzers>false</RunAnalyzers>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="Generated.cs" />
    <Compile Include="UnityStubs.cs" />
  </ItemGroup>
</Project>
"""

        (folder / "Validation.csproj").write_text(
            project,
            encoding="utf-8"
        )

        # Prevent dependency downloads.
        config = """<?xml version="1.0"?>
<configuration>
  <packageSources>
    <clear />
  </packageSources>
</configuration>
"""

        (folder / "NuGet.Config").write_text(
            config,
            encoding="utf-8"
        )

        restored = run_command(
            [
                "dotnet", "restore",
                "Validation.csproj",
                "--configfile", "NuGet.Config",
                "--ignore-failed-sources",
                "--verbosity", "quiet"
            ],
            folder
        )

        if restored is None or restored.returncode != 0:
            return False, error_lines(restored)

        built = run_command(
            [
                "dotnet", "build",
                "Validation.csproj",
                "--no-restore",
                "--nologo",
                "--verbosity", "quiet",
                "-p:UseSharedCompilation=false",
                "-p:RunAnalyzers=false"
            ],
            folder
        )

        if built is None or built.returncode != 0:
            return False, error_lines(built)

        return True, []


def compile_python(source):
    try:
        compile(
            source,
            "<generated-python>",
            "exec",
            dont_inherit=True
        )
        return True, []

    except (SyntaxError, ValueError, MemoryError) as error:
        return False, [str(error)]


def main():
    print("LocalAI Optimizer v0.5.0-dev")
    print("=== REAL COMPILATION VALIDATOR ===")

    reports = sorted(
        RESULTS.glob("code_quality_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True
    )

    if not reports:
        print("No code-quality reports found.")
        print("Run code_quality_engine.py first.")
        return

    latest = reports[0]

    print("Source report:", latest.name)

    report = json.loads(
        latest.read_text(encoding="utf-8")
    )

    framework = get_dotnet_framework()

    results = {
        "baseline": {},
        "optimized": {}
    }

    for configuration in ("baseline", "optimized"):
        tasks = report.get(
            "results", {}
        ).get(configuration, {})

        for task_id, task in tasks.items():
            print("\n" + "=" * 60)
            print("Configuration:", configuration)
            print("Task:", task_id)

            source = task.get("generated_code", "")
            language = task.get("language", "")

            if not isinstance(source, str) or not source.strip():
                passed = False
                errors = ["No generated source code."]

            elif len(source) > 50000:
                passed = False
                errors = ["Source exceeds validation size limit."]

            elif language == "Python":
                passed, errors = compile_python(source)

            elif language == "Unity C#":
                passed, errors = compile_csharp(
                    source,
                    framework
                )

            else:
                passed = False
                errors = ["Unsupported language."]

            results[configuration][task_id] = {
                "language": language,
                "compiled": passed,
                "errors": errors
            }

            print(
                "Compilation:",
                "PASS" if passed else "FAIL"
            )

            for error in errors:
                print("  ", error)

    baseline = results["baseline"]
    optimized = results["optimized"]

    common = set(baseline) & set(optimized)

    regressions = sorted(
        task for task in common
        if baseline[task]["compiled"]
        and not optimized[task]["compiled"]
    )

    improvements = sorted(
        task for task in common
        if not baseline[task]["compiled"]
        and optimized[task]["compiled"]
    )

    baseline_count = sum(
        item["compiled"] for item in baseline.values()
    )

    optimized_count = sum(
        item["compiled"] for item in optimized.values()
    )

    complete = (
        report.get("completed") is True
        and set(baseline) == set(optimized)
        and bool(baseline)
    )

    print("\n=== COMPILATION RESULTS ===")
    print("Completed:", complete)
    print(
        "Baseline compiled:",
        f"{baseline_count}/{len(baseline)}"
    )
    print(
        "Optimized compiled:",
        f"{optimized_count}/{len(optimized)}"
    )
    print("Regressions:", regressions)
    print("Improvements:", improvements)

    if not complete:
        status = "INCOMPLETE"
    elif regressions:
        status = "COMPILATION_REGRESSION_DETECTED"
    else:
        status = "NO_COMPILATION_REGRESSION_IN_TESTED_CASES"

    print("Status:", status)

    print("Generated code executed: False")
    print("Actual Unity compilation: False")

    output = {
        "source_report": latest.name,
        "model": report.get("model"),
        "dotnet_framework": framework,
        "completed": complete,
        "status": status,
        "regressions": regressions,
        "improvements": improvements,
        "results": results,
        "generated_code_executed": False,
        "actual_unity_compilation": False
    }

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    path = RESULTS / f"compilation_{stamp}.json"

    path.write_text(
        json.dumps(output, indent=4),
        encoding="utf-8"
    )

    print("\nReport saved:", path)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Compilation validator failed:", error)
