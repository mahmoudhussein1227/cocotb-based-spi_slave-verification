"""Run the SPI Cocotb regression with multiple seeds and merge coverage."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET

import yaml


SIM_DIR = Path(__file__).resolve().parent
DEFAULT_SEEDS = (104729, 130363, 155921, 181081, 205759)


def merge_coverage_reports(report_paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Sum bin hits across reports and recalculate merged coverage percentages."""
    if not report_paths:
        raise ValueError("No coverage reports were provided.")

    reports = []
    for report_path in report_paths:
        with report_path.open(encoding="utf-8") as report_file:
            reports.append(yaml.safe_load(report_file))

    names = set(reports[0])
    for report_path, report in zip(report_paths[1:], reports[1:]):
        if set(report) != names:
            raise ValueError(
                f"Coverage points in {report_path} do not match the first report."
            )

    merged = copy.deepcopy(reports[0])
    for name in names:
        node = merged[name]
        bin_key = "bins:_hits"
        if bin_key not in node:
            continue

        bin_names = set(node[bin_key])
        for report_path, report in zip(report_paths, reports):
            current_node = report[name]
            if bin_key not in current_node or set(current_node[bin_key]) != bin_names:
                raise ValueError(
                    f"Coverage bins for {name!r} differ in {report_path}."
                )
            for metadata in ("size", "at_least", "weight"):
                if current_node.get(metadata) != node.get(metadata):
                    raise ValueError(
                        f"Coverage metadata {metadata!r} for {name!r} differs "
                        f"in {report_path}."
                    )

        node[bin_key] = {
            bin_name: sum(report[name][bin_key][bin_name] for report in reports)
            for bin_name in node[bin_key]
        }
        threshold = node.get("at_least", 1)
        weight = node.get("weight", 1)
        node["coverage"] = sum(
            hits >= threshold for hits in node[bin_key].values()
        ) * weight
        node["cover_percentage"] = (
            round(100 * node["coverage"] / node["size"], 2)
            if node["size"]
            else 100.0
        )

    # Aggregate parent nodes (for example, "spi") from their direct children.
    for name in sorted(names, key=lambda item: item.count("."), reverse=True):
        node = merged[name]
        if "bins:_hits" in node:
            continue
        prefix = f"{name}."
        children = [
            merged[child]
            for child in names
            if child.startswith(prefix) and "." not in child[len(prefix):]
        ]
        if children:
            node["coverage"] = sum(child["coverage"] for child in children)
            node["size"] = sum(child["size"] for child in children)
            node["cover_percentage"] = (
                round(100 * node["coverage"] / node["size"], 2)
                if node["size"]
                else 100.0
            )

    return merged


def parse_cocotb_results(results_path: Path) -> dict[str, Any]:
    """Extract test counts and failure messages from a Cocotb xUnit report."""
    root = ET.parse(results_path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    if not suites:
        raise ValueError(f"No testsuite entries found in {results_path}.")

    counts = {
        key: sum(int(suite.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    failures = []
    for testcase in root.findall(".//testcase"):
        for kind in ("failure", "error"):
            result = testcase.find(kind)
            if result is not None:
                failures.append(
                    {
                        "test": testcase.get("name", "<unknown>"),
                        "type": kind,
                        "message": result.get("message", "").splitlines()[0],
                    }
                )

    counts["failure_details"] = failures
    counts["passed"] = (
        counts["tests"] > 0
        and counts["failures"] == 0
        and counts["errors"] == 0
    )
    return counts


def run_regression(seeds: tuple[int, ...], output_dir: Path, make_command: str) -> int:
    if len(seeds) != 5 or len(set(seeds)) != 5:
        raise ValueError("Provide exactly five distinct seeds.")

    make_executable = shutil.which(make_command)
    if make_executable is None:
        raise FileNotFoundError(
            f"Cannot find {make_command!r}. Install GNU Make or pass its path "
            "with --make."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    run_results: list[dict[str, Any]] = []
    coverage_paths: list[Path] = []

    for seed in seeds:
        run_dir = output_dir / f"seed_{seed}"
        run_dir.mkdir(parents=True, exist_ok=True)
        results_path = (run_dir / "results.xml").resolve()
        coverage_path = (run_dir / "coverage.yaml").resolve()
        log_path = (run_dir / "simulator.log").resolve()
        results_path.unlink(missing_ok=True)
        coverage_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment["COCOTB_RANDOM_SEED"] = str(seed)
        environment["COCOTB_RESULTS_FILE"] = str(results_path)
        environment["SPI_COVERAGE_FILE"] = str(coverage_path)

        command = [
            make_executable,
            "-C",
            str(SIM_DIR),
            "GUI=0",
            "WAVES=0",
        ]
        print(f"\n=== Running regression with seed {seed} ===", flush=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            completed = subprocess.run(
                command,
                env=environment,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=False,
            )
        print(
            f"=== Finished seed {seed} with exit code "
            f"{completed.returncode} ===",
            flush=True,
        )
        has_results = results_path.is_file()
        has_coverage = coverage_path.is_file()
        cocotb_results = None
        result_parse_error = None
        if has_results:
            try:
                cocotb_results = parse_cocotb_results(results_path)
            except (ET.ParseError, ValueError) as error:
                result_parse_error = str(error)

        run_results.append(
            {
                "seed": seed,
                "simulator_returncode": completed.returncode,
                "results_file": has_results,
                "coverage_file": has_coverage,
                "log_file": str(log_path),
                "cocotb": cocotb_results,
                "results_parse_error": result_parse_error,
            }
        )

        if has_coverage:
            coverage_paths.append(coverage_path)
        else:
            print(f"Missing coverage report for seed {seed}: {coverage_path}")
        if not has_results:
            print(f"Missing Cocotb results for seed {seed}: {results_path}")
        if cocotb_results is not None:
            print(
                f"  Cocotb tests: {cocotb_results['tests']}, "
                f"failures: {cocotb_results['failures']}, "
                f"errors: {cocotb_results['errors']}, "
                f"skipped: {cocotb_results['skipped']}"
            )
            for failure in cocotb_results["failure_details"]:
                print(
                    f"  {failure['type']} in {failure['test']}: "
                    f"{failure['message']}"
                )

    summary_path = output_dir / "regression_summary.json"
    summary_path.write_text(
        json.dumps({"runs": run_results}, indent=2) + "\n", encoding="utf-8"
    )

    if len(coverage_paths) != len(seeds):
        print("Cannot merge coverage: one or more runs did not produce a report.")
        return 1

    merged = merge_coverage_reports(coverage_paths)
    merged_path = output_dir / "coverage_merged.yaml"
    with merged_path.open("w", encoding="utf-8") as report_file:
        yaml.safe_dump(merged, report_file, sort_keys=False)

    print(f"\nMerged coverage: {merged_path}")
    print(f"Run summary: {summary_path}")
    test_failure_seeds = [
        run["seed"]
        for run in run_results
        if run["cocotb"] is not None and not run["cocotb"]["passed"]
    ]
    infrastructure_failure_seeds = [
        run["seed"]
        for run in run_results
        if run["simulator_returncode"] != 0
        and (run["cocotb"] is None or run["cocotb"]["passed"])
    ]
    invalid_results_seeds = [
        run["seed"]
        for run in run_results
        if not run["results_file"] or run["results_parse_error"] is not None
    ]
    missing_coverage_seeds = [
        run["seed"] for run in run_results if not run["coverage_file"]
    ]

    if test_failure_seeds:
        print(f"Cocotb test failures occurred for seeds: {test_failure_seeds}")
    if infrastructure_failure_seeds:
        print(
            "Simulator/Make exited nonzero despite passing or missing Cocotb "
            f"results for seeds: {infrastructure_failure_seeds}"
        )
    if invalid_results_seeds:
        print(f"Missing or invalid Cocotb results for seeds: {invalid_results_seeds}")
    if missing_coverage_seeds:
        print(f"Missing coverage reports for seeds: {missing_coverage_seeds}")
    if any(
        (
            test_failure_seeds,
            infrastructure_failure_seeds,
            invalid_results_seeds,
            missing_coverage_seeds,
        )
    ):
        print(f"Inspect each seed's simulator.log under {output_dir}.")
        return 1

    print("All five regression runs passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds",
        nargs=5,
        type=int,
        default=DEFAULT_SEEDS,
        metavar=("SEED1", "SEED2", "SEED3", "SEED4", "SEED5"),
        help="five distinct seeds (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SIM_DIR / "regression_runs",
        help="directory for per-seed outputs and merged coverage",
    )
    parser.add_argument(
        "--make",
        default="make",
        help="make executable (default: make)",
    )
    args = parser.parse_args()

    try:
        return run_regression(tuple(args.seeds), args.output_dir.resolve(), args.make)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Regression runner failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
