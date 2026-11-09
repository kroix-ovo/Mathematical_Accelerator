"""Run isolated, alternating software-only measurements of the four worktrees."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

BASELINE = "98be2be8e8b0fd9e1f600631fb54dae9638ce726"
VARIANTS = ("software-control", "python-optimized", "rust-hybrid", "c-hybrid")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--worktrees", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--samples", type=int, default=1001)
    parser.add_argument("--tests", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    trees = {name: (args.worktrees / name).resolve() for name in VARIANTS}
    control = trees["software-control"]
    head = subprocess.check_output(["git", "-C", str(control), "rev-parse", "HEAD"], text=True).strip()
    if head != BASELINE:
        raise RuntimeError("control HEAD changed")
    subprocess.run(["git", "-C", str(control), "diff", "--exit-code", "HEAD"], check=True)
    source_hashes = {name: hashlib.sha256((tree / "sw/mathaccel/driver/framing.py").read_bytes()).hexdigest()
                     for name, tree in trees.items() if name != "software-control"}
    if len(set(source_hashes.values())) != 1:
        raise RuntimeError("experimental Python sources diverged")
    for name, tree in trees.items():
        # All remaining Python production files must be the unchanged baseline.
        changed = subprocess.check_output(["git", "-C", str(tree), "diff", "--name-only", BASELINE,
                                           "--", "sw/mathaccel"], text=True).splitlines()
        if set(changed) - {"sw/mathaccel/driver/framing.py"}:
            raise RuntimeError(f"uncontrolled production changes in {name}: {changed}")
    envs = {}
    for name, tree in trees.items():
        envs[name] = dict(os.environ, PYTHONPATH=str(tree / "sw"),
                          PYTHONHASHSEED="0", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                          MATHACCEL_FRAMING="native" if name in ("rust-hybrid", "c-hybrid") else "python")
    if args.tests:
        for name, tree in trees.items():
            for suffix, extra in (("software", []), ("udp", ["sw/tests/test_device.py", "--port", "udpsim"])):
                print(f"testing {name}/{suffix}", flush=True)
                with (out / f"{name}-{suffix}.txt").open("w") as stream:
                    subprocess.run([sys.executable, "-m", "pytest", "-q", *extra], cwd=tree,
                                   env=envs[name], stdout=stream, stderr=subprocess.STDOUT, check=True)
    harness = Path(__file__).with_name("language_bench.py")
    rounds = []
    for round_id in range(args.rounds):
        # Four rotations put every variant in every measurement position.
        order = list(VARIANTS[round_id % 4:] + VARIANTS[:round_id % 4])
        for name in order:
            target = out / f"round-{round_id}-{name}.json"
            print(f"round {round_id}: {name}", flush=True)
            subprocess.run([sys.executable, str(harness), "--root", str(trees[name]),
                            "--output", str(target), "--samples", str(args.samples)],
                           cwd=trees[name], env=envs[name], check=True)
            result = json.loads(target.read_text())
            expected = {"software-control": "original-python", "python-optimized": "python",
                        "rust-hybrid": "rust", "c-hybrid": "c"}[name]
            if result["backend"] != expected:
                raise RuntimeError(f"wrong backend: {name}: {result['backend']}")
            rounds.append((name, target.name, result))
    reference = rounds[0][2]["fingerprints"]
    for name, filename, result in rounds:
        if result["fingerprints"] != reference:
            raise AssertionError(f"output differential failed: {filename}")
    summary = {"baseline": BASELINE, "python_source_sha256": source_hashes,
               "fingerprints_equal": True, "raw_files": [r[1] for r in rounds], "variants": {}}
    for name in VARIANTS:
        runs = [r[2] for r in rounds if r[0] == name]
        summary["variants"][name] = {
            "head": runs[0]["head"],
            "peak_rss_kib_median": statistics.median(r["peak_rss_kib"] for r in runs),
            "cases": {case: {"median_ns": statistics.median(r["benchmarks"][case]["median_ns"] for r in runs),
                              "round_medians_ns": [r["benchmarks"][case]["median_ns"] for r in runs]}
                      for case in runs[0]["benchmarks"]}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("All compiler, image, wire, result, flag and model-cycle fingerprints match.", flush=True)


if __name__ == "__main__":
    main()
