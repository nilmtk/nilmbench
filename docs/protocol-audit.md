# Protocol audit

This document separates three things that were previously easy to conflate:
the executed notebooks, the NILMBench2026 paper, and the protocol now used for
new leaderboard runs.

## Evidence recovered

The executed notebooks were recovered from the `result_notebooks` directory of
an installed `nilmtk_contrib` environment on the Ramanujan server. They are not
committed here because they contain bulky cell outputs and machine-specific
paths. The key inputs can be independently identified by these SHA-256 hashes:

| Notebook | SHA-256 |
| --- | --- |
| `redd-t1-1min-seed10(hyperparam).ipynb` | `248167b6c19f5668ebc90dc1fbe9caed2eb06ae8313e956c94cc6727a3b8bf31` |
| `ukdale-t1-1min-seed10(hyperparam).ipynb` | `0616835fbc9a5532b4fb2dce14b38145a3d6657e5362efc2c12a7af1af3f2b61` |
| `refit-t1-1min.ipynb` | `06199acc2a93031e997534b01c2421c48b3553c22ee100f9dfa438b69ed83824` |
| `redd-t2.ipynb` | `483c6a5cf8c4e07b1e77854e00a44ac4c4815e4e006695221d94c68c96e0955e` |
| `redd-t2-f1score.ipynb` | `4725094a469903cb8575720137c866c6b4660237374d8f796c3772b5aefca807` |
| `ukdale-t2.ipynb` | `68d75b071a27e68eeced367582ecdef44e821cd05d4ceed8132cc123571ae5b5` |
| `refit-t2-seed10.ipynb` | `f2f13e57ea2fc5d21037518ab219a872d7d792f1d8c1beea4d69a859cd5d9efc` |
| `redd-refit-t3.ipynb` | `68c1a29d7473954951ff29670deeda549d1a19564057dd279fbfe6eb016b8813` |
| `redd-refit-t3 to refit-redd.ipynb` | `92124fcbb3818f86514cd274de850945f817b5d69d038518d53450611de3e8f1` |

## Recovered differences

- The REDD T1 notebook requested 1–30 April 2011, but the canonical converted
  file begins on 18 April. A legacy run therefore uses only part of its
  requested training window.
- The REFIT T1 notebook requests a microwave in building 1. That building has
  no microwave in the canonical conversion.
- The REDD T2 MAE notebook tests building 4, while the REDD T2 F1 notebook and
  Section 3.4 of the paper test building 6. Building 4 also lacks the requested
  fridge in the canonical conversion.
- Executed notebook F1 uses `nilmtk.losses.f1score`, which applies a fixed 10 W
  threshold. Section 3.5 of the paper specifies appliance thresholds instead.

The `historical-*` tasks preserve the recovered notebook inputs and legacy 10 W
metric behavior for forensic reproduction. They may warn or fail where the
hashed canonical files cannot satisfy those requests.

The `corrected-*` tasks are the basis for new claims. They use the paper's
building splits, appliance-specific thresholds, strict time-envelope checks,
and per-appliance alignment. In particular, corrected REDD T2 is B1/B2/B3 →
B6 and uses fridge, washing machine, and dish washer.

## NILM Metadata resolution

Task configurations use canonical NILM Metadata names. NILMTK resolves those
names through its appliance taxonomy and synonyms; NILMbench does not maintain
a competing alias table. Every observed window records:

- the exact resolved appliance types and instances;
- the exact meter or MeterGroup identifiers;
- unrelated appliances sharing a selected physical meter; and
- the active/apparent power type selected from the configured preference list.

This audit surfaced real data-quality constraints. Examples include:

- REDD `washing machine` resolving to `washer dryer`, including multi-channel
  MeterGroups in buildings 1 and 3;
- UK-DALE building 4 using apparent mains power while buildings 1 and 2 use
  active mains power;
- UK-DALE building 4 microwave and washing machine resolving to the same shared
  meter, which also contains a breadmaker; and
- REFIT building 1 `fridge` resolving to one fridge and two freezers.

Paper-compatible corrected tasks warn and record shared-meter contamination.
A future clean-meter profile can set `shared_meter_policy = "strict"` and use
only targets with isolated ground truth.

## Real-data preflight

On 17 July 2026, all eight corrected tasks passed a read-only preflight against
the three SHA-256-pinned HDF5 files on Ramanujan. The check loaded each train and
test building, resolved every canonical appliance through NILM Metadata,
selected an available AC power type, and found aligned samples. This is a data
and protocol preflight; it is not a replacement for the CUDA model benchmark.

## Release sequence

Model implementations and their model-level tests merge in `nilmtk-contrib`
first. NILMbench then advances its single `nilmtk-contrib` dependency and
container build context to the reviewed batch commit, refreshes `uv.lock`, and
runs CPU and CUDA checks against that same revision. ModernTCN and DLinear were
adopted together at `8d745493ed9f84dd00fb502ffe85943eaeedc4c8`, avoiding a
separate benchmark image for every algorithm while preserving an immutable
campaign environment. TimesNet and SGN were similarly batched at
`c130293e24e16817b9859d1b78ae18bd988b1219` after their independent model and
shared-runtime PRs passed. TSMixer then advanced the same single image family to
`5767286078f5853a2ef1d6f431eb95a1c47ba4e8`; the image still contains the
complete model suite and is not a model-specific image. NILMMoE then advanced
that same image family to `9d28870d0df378c34092d95ea8a11479e0fe7db3`.
