
import ast
import json
import operator

from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

MAX_STEPS = 5000
MAX_ITEMS = 64

CASES = {
    "python_clamp": [
        ((-2, 0, 10), 0),
        ((5, 0, 10), 5),
        ((17, 0, 10), 10),
        ((0, 0, 10), 0),
        ((10, 0, 10), 10),
        ((-8, -5, -1), -5),
        ((-3, -5, -1), -3),
        ((5, -5, -1), -1),
    ],
    "python_even": [
        (([],), 0),
        (([1, 2, 3, 4],), 2),
        (([-4, -3, -2, -1, 0],), 3),
        (([2, 2, 2],), 3),
        (([1, 3, 5],), 0),
        (([100, 102, 103, 104],), 3),
    ],
}

FUNCTIONS = {
    "python_clamp": "clamp_number",
    "python_even": "count_even",
}

OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

SAFE_FUNCTIONS = {
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "abs": abs,
    "range": range,
}


class UnsupportedCode(Exception):
    pass


class Returned(Exception):
    def __init__(self, value):
        self.value = value


def tick(steps):
    steps[0] += 1

    if steps[0] > MAX_STEPS:
        raise UnsupportedCode(
            "Interpreter operation limit exceeded"
        )


def evaluate(node, variables, steps):
    tick(steps)

    if isinstance(node, ast.Constant):
        if type(node.value) in (
            int, float, bool, str, type(None)
        ):
            return node.value

        raise UnsupportedCode(
            "Unsupported literal"
        )

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise UnsupportedCode(
                "Unknown variable: " + node.id
            )

        return variables[node.id]

    if isinstance(node, (ast.List, ast.Tuple)):
        values = [
            evaluate(item, variables, steps)
            for item in node.elts
        ]

        if len(values) > MAX_ITEMS:
            raise UnsupportedCode(
                "Collection too large"
            )

        return values if isinstance(
            node, ast.List
        ) else tuple(values)

    if isinstance(node, ast.UnaryOp):
        value = evaluate(
            node.operand, variables, steps
        )

        if isinstance(node.op, ast.Not):
            return not value

        if isinstance(node.op, ast.USub):
            return -value

        if isinstance(node.op, ast.UAdd):
            return +value

    if isinstance(node, ast.BinOp):
        operation = OPERATORS.get(
            type(node.op)
        )

        if operation is None:
            raise UnsupportedCode(
                "Unsupported binary operation"
            )

        left = evaluate(
            node.left, variables, steps
        )
        right = evaluate(
            node.right, variables, steps
        )

        if type(left) not in (int, float, bool):
            raise UnsupportedCode(
                "Non-numeric binary operand"
            )

        if type(right) not in (int, float, bool):
            raise UnsupportedCode(
                "Non-numeric binary operand"
            )

        return operation(left, right)

    if isinstance(node, ast.Compare):
        left = evaluate(
            node.left, variables, steps
        )

        for op, comparator in zip(
            node.ops, node.comparators
        ):
            operation = COMPARISONS.get(
                type(op)
            )

            if operation is None:
                raise UnsupportedCode(
                    "Unsupported comparison"
                )

            right = evaluate(
                comparator, variables, steps
            )

            if not operation(left, right):
                return False

            left = right

        return True

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for value in node.values:
                if not evaluate(
                    value, variables, steps
                ):
                    return False

            return True

        if isinstance(node.op, ast.Or):
            for value in node.values:
                if evaluate(
                    value, variables, steps
                ):
                    return True

            return False

    if isinstance(node, ast.IfExp):
        condition = evaluate(
            node.test, variables, steps
        )

        return evaluate(
            node.body if condition
            else node.orelse,
            variables,
            steps
        )

    if isinstance(
        node, (ast.GeneratorExp, ast.ListComp)
    ):
        if len(node.generators) != 1:
            raise UnsupportedCode(
                "Only one comprehension loop supported"
            )

        generator = node.generators[0]

        if (
            generator.is_async
            or not isinstance(
                generator.target, ast.Name
            )
        ):
            raise UnsupportedCode(
                "Unsupported comprehension"
            )

        source = evaluate(
            generator.iter, variables, steps
        )

        if not isinstance(
            source, (list, tuple, range)
        ) or len(source) > MAX_ITEMS:
            raise UnsupportedCode(
                "Unsupported comprehension source"
            )

        output = []

        for value in source:
            tick(steps)

            local = variables.copy()
            local[generator.target.id] = value

            if all(
                evaluate(test, local, steps)
                for test in generator.ifs
            ):
                output.append(
                    evaluate(node.elt, local, steps)
                )

        return output

    if isinstance(node, ast.Call):
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id not in SAFE_FUNCTIONS
            or node.keywords
        ):
            raise UnsupportedCode(
                "Function call not allowed"
            )

        arguments = [
            evaluate(arg, variables, steps)
            for arg in node.args
        ]

        if node.func.id == "range":
            result = range(*arguments)

            if len(result) > MAX_ITEMS:
                raise UnsupportedCode(
                    "Range too large"
                )

            return result

        return SAFE_FUNCTIONS[
            node.func.id
        ](*arguments)

    raise UnsupportedCode(
        "Unsupported expression: "
        + type(node).__name__
    )


def execute_statements(
    statements, variables, steps
):
    for node in statements:
        tick(steps)

        if isinstance(node, ast.Return):
            value = (
                evaluate(node.value, variables, steps)
                if node.value is not None
                else None
            )

            raise Returned(value)

        if isinstance(node, ast.If):
            condition = evaluate(
                node.test, variables, steps
            )

            execute_statements(
                node.body if condition
                else node.orelse,
                variables,
                steps
            )

            continue

        if isinstance(node, ast.Assign):
            if (
                len(node.targets) != 1
                or not isinstance(
                    node.targets[0], ast.Name
                )
            ):
                raise UnsupportedCode(
                    "Unsupported assignment"
                )

            value = evaluate(
                node.value, variables, steps
            )

            variables[
                node.targets[0].id
            ] = value

            continue

        if isinstance(node, ast.AnnAssign):
            if (
                not isinstance(
                    node.target, ast.Name
                )
                or node.value is None
            ):
                raise UnsupportedCode(
                    "Unsupported annotated assignment"
                )

            variables[node.target.id] = evaluate(
                node.value, variables, steps
            )

            continue

        if isinstance(node, ast.AugAssign):
            if not isinstance(
                node.target, ast.Name
            ):
                raise UnsupportedCode(
                    "Unsupported augmented assignment"
                )

            operation = OPERATORS.get(
                type(node.op)
            )

            if operation is None:
                raise UnsupportedCode(
                    "Unsupported assignment operator"
                )

            name = node.target.id

            if name not in variables:
                raise UnsupportedCode(
                    "Unknown variable: " + name
                )

            value = evaluate(
                node.value, variables, steps
            )

            variables[name] = operation(
                variables[name], value
            )

            continue

        if isinstance(node, ast.For):
            if (
                not isinstance(
                    node.target, ast.Name
                )
                or node.orelse
            ):
                raise UnsupportedCode(
                    "Unsupported for loop"
                )

            items = evaluate(
                node.iter, variables, steps
            )

            if not isinstance(
                items, (list, tuple, range)
            ) or len(items) > MAX_ITEMS:
                raise UnsupportedCode(
                    "Unsafe or oversized iteration"
                )

            for value in items:
                tick(steps)

                variables[
                    node.target.id
                ] = value

                execute_statements(
                    node.body,
                    variables,
                    steps
                )

            continue

        if isinstance(node, ast.Pass):
            continue

        if (
            isinstance(node, ast.Expr)
            and isinstance(
                node.value, ast.Constant
            )
            and isinstance(
                node.value.value, str
            )
        ):
            continue

        raise UnsupportedCode(
            "Unsupported statement: "
            + type(node).__name__
        )


def prepare_function(source, task_id):
    tree = ast.parse(source)

    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
    ]

    if len(functions) != 1:
        raise UnsupportedCode(
            "Expected exactly one function"
        )

    function = functions[0]

    if function.name != FUNCTIONS[task_id]:
        raise UnsupportedCode(
            "Unexpected function name"
        )

    if (
        function.decorator_list
        or function.args.defaults
        or function.args.kw_defaults
        or function.args.vararg
        or function.args.kwarg
        or function.args.kwonlyargs
        or function.args.posonlyargs
    ):
        raise UnsupportedCode(
            "Unsupported function signature"
        )

    expected_count = len(
        CASES[task_id][0][0]
    )

    if len(function.args.args) != expected_count:
        raise UnsupportedCode(
            "Unexpected parameter count"
        )

    for node in tree.body:
        if node is function:
            continue

        if (
            isinstance(node, ast.Expr)
            and isinstance(
                node.value, ast.Constant
            )
            and isinstance(
                node.value.value, str
            )
        ):
            continue

        raise UnsupportedCode(
            "Extra top-level code is not allowed"
        )

    return function


def simulate(function, arguments):
    names = [
        item.arg
        for item in function.args.args
    ]

    variables = dict(
        zip(names, arguments)
    )

    steps = [0]

    try:
        execute_statements(
            function.body, variables, steps
        )
    except Returned as returned:
        return returned.value

    return None


def check_python(source, task_id):
    try:
        function = prepare_function(
            source, task_id
        )
    except (
        SyntaxError, UnsupportedCode
    ) as error:
        return {
            "status": "INCONCLUSIVE",
            "passed": 0,
            "total": len(CASES[task_id]),
            "details": [str(error)]
        }

    passed = 0
    details = []

    for arguments, expected in CASES[task_id]:
        try:
            actual = simulate(
                function, arguments
            )

            correct = (
                type(actual) is type(expected)
                and actual == expected
            )

            if correct:
                passed += 1

            details.append({
                "input": arguments,
                "expected": expected,
                "actual": actual,
                "passed": correct
            })

        except UnsupportedCode as error:
            return {
                "status": "INCONCLUSIVE",
                "passed": passed,
                "total": len(CASES[task_id]),
                "details": [str(error)]
            }

        except Exception as error:
            details.append({
                "input": arguments,
                "expected": expected,
                "error": type(error).__name__,
                "passed": False
            })

    return {
        "status": (
            "PASS"
            if passed == len(CASES[task_id])
            else "FAIL"
        ),
        "passed": passed,
        "total": len(CASES[task_id]),
        "details": details
    }


def latest_report(pattern):
    files = sorted(
        RESULTS.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    return files[0] if files else None


def main():
    print("LocalAI Optimizer v0.5.0-dev")
    print("=== BEHAVIOR VALIDATION ENGINE ===")

    source_path = latest_report(
        "code_quality_*.json"
    )

    if source_path is None:
        print("No code-quality report found.")
        return

    source = json.loads(
        source_path.read_text(encoding="utf-8")
    )

    print("Source:", source_path.name)
    print("Model:", source.get("model"))

    compilation_path = None
    compilation = None

    for path in sorted(
        RESULTS.glob("compilation_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    ):
        candidate = json.loads(
            path.read_text(encoding="utf-8")
        )

        if (
            candidate.get("source_report")
            == source_path.name
        ):
            compilation_path = path
            compilation = candidate
            break

    behavior = {
        "baseline": {},
        "optimized": {}
    }

    csharp = {
        "baseline": {},
        "optimized": {}
    }

    for config in ("baseline", "optimized"):
        tasks = source.get(
            "results", {}
        ).get(config, {})

        for task_id, task in tasks.items():
            if task_id in CASES:
                result = check_python(
                    task.get("generated_code", ""),
                    task_id
                )

                behavior[config][task_id] = result

                print(
                    config, task_id,
                    result["status"],
                    f'{result["passed"]}/{result["total"]}'
                )

            elif task_id.startswith("unity_"):
                compiled = None

                if compilation is not None:
                    compiled = compilation.get(
                        "results", {}
                    ).get(config, {}).get(
                        task_id, {}
                    ).get("compiled")

                csharp[config][task_id] = {
                    "compilation_passed": compiled,
                    "behavior_status": "NOT_TESTED"
                }

    regressions = []
    improvements = []

    python_tasks = set(
        behavior["baseline"]
    ) & set(
        behavior["optimized"]
    )

    inconclusive = False

    for task_id in sorted(python_tasks):
        baseline = behavior[
            "baseline"
        ][task_id]["status"]

        optimized = behavior[
            "optimized"
        ][task_id]["status"]

        if "INCONCLUSIVE" in (
            baseline, optimized
        ):
            inconclusive = True

        if baseline == "PASS" and optimized == "FAIL":
            regressions.append(task_id)

        if baseline == "FAIL" and optimized == "PASS":
            improvements.append(task_id)

    expected_tasks = set(CASES) & set(
        source.get("results", {}).get(
            "baseline", {}
        )
    )

    if not expected_tasks.issubset(python_tasks):
        inconclusive = True

    if regressions:
        status = "PYTHON_BEHAVIOR_REGRESSION"
    elif inconclusive:
        status = "INCONCLUSIVE"
    else:
        status = "NO_PYTHON_BEHAVIOR_REGRESSION_DETECTED"

    print("\n=== BEHAVIOR VALIDATION RESULTS ===")
    print("Model:", source.get("model"))
    print("Python tasks compared:", len(python_tasks))

    for config in ("baseline", "optimized"):
        total = sum(
            item["total"]
            for item in behavior[config].values()
        )
        passed = sum(
            item["passed"]
            for item in behavior[config].values()
        )

        print(
            f"{config} Python cases:",
            f"{passed}/{total}"
        )

    print("Regressions:", regressions)
    print("Improvements:", improvements)
    print("Status:", status)

    print(
        "Unity C# behavior: NOT TESTED"
    )
    print(
        "Generated code executed: False"
    )

    report = {
        "schema_version": 1,
        "model": source.get("model"),
        "model_digest": source.get("model_digest"),
        "source_quality_report": source_path.name,
        "source_compilation_report": (
            compilation_path.name
            if compilation_path else None
        ),
        "status": status,
        "regressions": regressions,
        "improvements": improvements,
        "python_behavior": behavior,
        "csharp_compilation_only": csharp,
        "generated_code_executed": False,
        "unity_behavior_tested": False,
        "full_quality_verified": False
    }

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    destination = RESULTS / (
        f"behavior_{stamp}.json"
    )

    destination.write_text(
        json.dumps(report, indent=4),
        encoding="utf-8"
    )

    print("Report saved:", destination)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Behavior validator failed:", error)
