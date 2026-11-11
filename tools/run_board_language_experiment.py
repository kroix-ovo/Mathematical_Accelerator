"""Rotate four host implementations against one already-programmed FPGA."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

from language_git import git_env

VARIANTS = ("software-control", "python-optimized", "rust-hybrid", "c-hybrid")
EXPECTED = dict(zip(VARIANTS, ("original-python", "python", "rust", "c"), strict=True))


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--port", default="udp:192.168.10.2")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    trees = {name: root.parent / name for name in VARIANTS}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((output / "board-manifest.json").read_text())
    bit = root.parents[1] / "build/vivado/nexys_top.bit"
    bit_hash = hashlib.sha256(bit.read_bytes()).hexdigest()
    if bit_hash != manifest["bitstream_sha256"]:
        raise AssertionError("recorded bitstream file changed")
    hashes = {name: hashlib.sha256((tree / "sw/mathaccel/driver/framing.py").read_bytes()).hexdigest()
              for name, tree in trees.items() if name != "software-control"}
    assert len(set(hashes.values())) == 1, "shared Python framing diverged"
    subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=trees["software-control"],
                   env=git_env(trees["software-control"]), check=True)
    records, reference = [], None
    for round_id in range(args.rounds):
        order = VARIANTS[round_id % 4:] + VARIANTS[:round_id % 4]
        for name in order:
            target = output / f"round-{round_id}-{name}.json"
            env = dict(git_env(trees[name]), PYTHONPATH=str(trees[name] / "sw"),
                       PYTHONHASHSEED="0", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                       MATHACCEL_FRAMING="native" if name in ("rust-hybrid", "c-hybrid") else "python")
            print(f"ROUND {round_id}: {name} physical FPGA", flush=True)
            command = [sys.executable, str(root / "tools/board_language_bench.py"),
                       "--root", str(trees[name]), "--output", str(target), "--port", args.port,
                       "--seconds", str(args.seconds)]
            with target.with_suffix(".txt").open("w") as log:
                subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            data = json.loads(target.read_text())
            assert data["backend"] == EXPECTED[name] and data["physical_board"]
            if reference is None:
                reference = data["fingerprints"]
            assert data["fingerprints"] == reference, f"board differential mismatch: {name}"
            assert all(v["run_requests"] == 1 for v in data["recovery"].values())
            records.append((name, target.name, data))
            print(f"PASS {name}: results/flags/cycles/images equal; RUN recovery passed", flush=True)
    summary = {"board_manifest": manifest, "port": args.port, "rounds": args.rounds,
               "seconds_per_sustained_window": args.seconds, "physical_board": True,
               "all_fingerprints_equal": True, "shared_python_hashes": hashes,
               "raw_files": [filename for name, filename, data in records], "variants": {}}
    for name in VARIANTS:
        runs = [data for variant, filename, data in records if variant == name]
        summary["variants"][name] = {
            "head": runs[0]["head"], "backend": runs[0]["backend"],
            "rss_kib_median": statistics.median(data["peak_rss_kib"] for data in runs),
            "cases": {case: {
                "median_ns": statistics.median(data["benchmarks"][case]["median_ns"] for data in runs),
                "round_medians_ns": [data["benchmarks"][case]["median_ns"] for data in runs],
                "MBps": statistics.median(data["benchmarks"][case]["payload_MBps"] for data in runs),
                "round_MBps": [data["benchmarks"][case]["payload_MBps"] for data in runs],
                "cpu_percent": statistics.median(data["benchmarks"][case]["cpu_percent"] for data in runs),
                "sample_count": sum(data["benchmarks"][case]["count"] for data in runs),
            } for case in runs[0]["benchmarks"]}}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    assert hashlib.sha256(bit.read_bytes()).hexdigest() == bit_hash
    print("COMPLETE: all four implementations passed on the same physical FPGA.", flush=True)


if __name__ == "__main__":
    main()
