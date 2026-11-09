# My divider experiment worktrees

I set these up on 2026-11-08. The main checkout stays on `main`, where I will
split the division and square-root units first. The five linked checkouts are
ready for the later divider experiments. They share Git history and objects,
but each has its own branch, index, working files and local build directory.

**The split is done.** I created `baseline/divider-experiments-start` at the
setup commit, and `baseline/divsqrt-split` at `02c785e` once the split passed
its gate. On 2026-11-09 I fast-forwarded all five worktrees to it with the
commands below and checked that they shared that full SHA before changing any
divider. The results are in [divider-comparison.md](divider-comparison.md).

| Directory under the main folder | Branch | What I will change later |
|---|---|---|
| `worktrees/div-two-step` | `experiment/div-two-step` | Two binary division recurrence steps per iteration clock |
| `worktrees/div-pipeline` | `experiment/div-pipeline` | Registered exact division recurrence with overlapping operations |
| `worktrees/div-srt4` | `experiment/div-srt4` | Radix-4 SRT division |
| `worktrees/div-dual-lane` | `experiment/div-dual-lane` | Two divider lanes alongside the one baseline sqrt unit |
| `worktrees/div-multiplicative` | `experiment/div-multiplicative` | Newton-Raphson or Goldschmidt division with exact final correction |

I keep the experiment checkouts out of the parent repository's tracked contents
with `/worktrees/` in `.gitignore`. I do not stage these folders as submodules
or copy source trees by hand. The experiment task is in
[divider-optimization-task.md](divider-optimization-task.md).

Each checkout has this local scaffold, ignored through its existing `build/` rule:

```text
build/experiment/
  README.md       which branch I am in, and the pending baseline gate
  logs/           simulation/build console logs
  reports/        routed timing, utilization and comparison reports
  results/        numerical results and benchmark output
```

I have not installed new environments, run candidate builds or programmed the
board as part of this setup. When I need Python tooling, I create an independent
`.venv` and editable installation in that worktree. An editable install from the
main checkout can otherwise import the wrong host implementation.

## After I finish and verify the split on main

I commit the verified split on `main`, then create the split tag at that commit:

```powershell
git tag -a baseline/divsqrt-split -m "My verified independent division and sqrt baseline"
```

I only run that command once the split is actually committed and validated.
The following commands are a later step, not part of the initial scaffold setup.
From the main project folder, I check and fast-forward each scaffold:

```powershell
$dividerTracks = @(
    'div-two-step', 'div-pipeline', 'div-srt4',
    'div-dual-lane', 'div-multiplicative'
)
foreach ($dividerTrack in $dividerTracks) {
    $dividerPath = Join-Path 'worktrees' $dividerTrack
    $dividerChanges = git -C $dividerPath status --porcelain
    if ($LASTEXITCODE -ne 0 -or $dividerChanges) {
        throw "I need to inspect changes in $dividerPath before updating it."
    }
    git -C $dividerPath merge --ff-only baseline/divsqrt-split
    if ($LASTEXITCODE -ne 0) {
        throw "I need to resolve the branch history in $dividerPath without resetting it."
    }
    $dividerHead = git -C $dividerPath rev-parse HEAD
    $dividerBaseline = git rev-parse 'baseline/divsqrt-split^{commit}'
    if ($dividerHead -ne $dividerBaseline) {
        throw "$dividerPath does not point at the common split baseline."
    }
}
```

This assumes I have kept the scaffolds clean and have not committed experiment
work before the split. If one has diverged, I inspect its changes and integrate
the common baseline deliberately. I do not reset or overwrite it to force a match.
I keep both baseline tags so the original setup and the later split stay distinct.

## How I check which checkout I am using

```powershell
git worktree list
git -C worktrees/div-two-step branch --show-current
git -C worktrees/div-two-step rev-parse HEAD
git -C worktrees/div-two-step status --short
```

I run build and test commands with that worktree as the working directory and
verify its source paths in the logs. I keep the sqrt RTL fixed after the split,
and record the candidate's full source SHA beside every result. These local
branches and tags have not been pushed by the setup task.
