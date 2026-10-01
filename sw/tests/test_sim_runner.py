"""Check the standalone cocotb runner's exit status without requiring a simulator.

I supply a runner that finishes normally and returns a prepared results file.
This reproduces the CI risk: simulator process success is insufficient if the
report contains failed assertions. Real RTL compilation is tested by rtl-sim.
"""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def invoke_runner(monkeypatch, report: Path) -> int:
    """Run the real main function with a fake build/test backend and XML output."""
    # run_path imports the script without entering its __main__ block. Its
    # function globals are patched locally so each test has an isolated runner.
    main = runpy.run_path(str(ROOT / "hw/tb/cocotb/run.py"))["main"]
    backend = SimpleNamespace(build=lambda **kw: None, test=lambda **kw: report)
    monkeypatch.setitem(main.__globals__, "get_runner", lambda sim: backend)
    monkeypatch.setattr("sys.argv", ["run.py", "--sim", "verilator"])
    return main()


@pytest.mark.parametrize(("tests", "failures", "errors", "expected"), [
    (1, 0, 0, 0),
    (1, 1, 0, 1),
    (1, 0, 1, 1),
    (0, 0, 0, 1),
])
def test_report_controls_exit_status(monkeypatch, tmp_path, tests, failures, errors, expected):
    """Pass completed tests, but reject failed, errored, or empty test reports."""
    report = tmp_path / "results.xml"
    report.write_text(
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" '
        f'errors="{errors}" /></testsuites>'
    )
    assert invoke_runner(monkeypatch, report) == expected


def test_missing_report_fails(monkeypatch, tmp_path):
    """A backend returning a nonexistent report must not produce successful main()."""
    with pytest.raises(RuntimeError, match="not found"):
        invoke_runner(monkeypatch, tmp_path / "missing.xml")
