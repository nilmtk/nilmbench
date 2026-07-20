# NILMbench

Reference implementation, experiment runner, and project website for
**[NILMBench2026](https://nilmtk.github.io/nilmbench/)** — *A Benchmark for
Energy Disaggregation* (BuildSys '26, **Best Paper Candidate**).

The paper evaluates **16 NILM models** across **3 datasets**, **2 sampling
resolutions**, and within-building, cross-building, and cross-dataset tasks. The
repository also maintains a provenance-checked leaderboard for results produced
after the fixed paper study.

## Ecosystem repositories

| Research task | Repository |
| --- | --- |
| Dataset conversion, meter access, preprocessing, and metrics | [NILMTK core](https://github.com/nilmtk/nilmtk) |
| Appliance taxonomy, synonyms, meter relationships, and dataset schema | [NILM Metadata](https://github.com/nilmtk/nilm_metadata) |
| Disaggregation model implementation and testing | [nilmtk-contrib](https://github.com/nilmtk/nilmtk-contrib) |
| Fixed T1/T2/T3 evaluation and published result bundles | **NILMbench — this repository** |

**Authors:** Aayush Kuloor\*, Anurag Singh\*, Harsh Dhru\*, Nipun Batra† · IIT Gandhinagar
(\* equal contribution, † corresponding author)

## Links

- **Paper:** https://sustainability-lab.github.io/papers/2026/nilmbench2026_buildsys.pdf
- **Model implementations:** https://github.com/nilmtk/nilmtk-contrib
- **Project website:** https://nilmtk.github.io/nilmbench/
- **Living leaderboard:** https://nilmtk.github.io/nilmbench/leaderboard.html

## What is reproducible here

The benchmark is now an installable command-line application rather than a set of order-dependent notebooks. It provides:

- typed TOML definitions for the paper's T1/T2/T3 protocols;
- explicit `historical-*` profiles reconstructed from the executed notebooks;
- strict corrected profiles that reject silently truncated data windows;
- canonical appliance resolution through NILM Metadata, with exact resolved
  appliance/meter identities and shared-circuit contamination in every result;
- ordered active/apparent power preferences resolved per dataset window;
- explicit legacy and paper appliance-threshold policies for F1;
- deterministic seeds and persistent, resumable Optuna SQLite studies;
- deterministic Torch algorithms with an explicit cuBLAS workspace policy;
- structured JSON and CSV results with source, dataset, runtime, parameter/FLOP, and container provenance;
- separate CPU-smoke and CUDA-benchmark containers.
- a NILMTK Mean sanity-check baseline alongside the contrib architectures.

The REDD, UK-DALE, and REFIT data are not redistributed. The runner expects user-provided NILMTK HDF5 conversions and verifies them against the recorded file sizes and SHA-256 digests. The exact protocol discrepancies recovered from the old notebooks are documented in the [protocol audit](https://github.com/nilmtk/nilmbench/blob/main/docs/protocol-audit.md).

## Install for development

Use Python 3.11, which is the version currently supported by nilmtk-contrib:

```bash
git clone https://github.com/nilmtk/nilmtk-contrib.git
git clone https://github.com/nilmtk/nilmbench.git
cd nilmbench

uv venv --python 3.11
source .venv/bin/activate
UV_TORCH_BACKEND=cpu uv pip install -e "../nilmtk-contrib[torch]" -e ".[runtime,dev]"
nilmbench list
pytest
```

For a non-editable install using the exact reviewed nilmtk-contrib revision
pinned by this repository:

```bash
uv venv --python 3.11
source .venv/bin/activate
UV_TORCH_BACKEND=cpu uv pip install ".[benchmark]"
```

## Data

Place the three converted datasets in one directory with these names:

```text
data/
├── redd.h5
├── refit.h5
└── ukdale.h5
```

Alternatively, set `NILMBENCH_REDD`, `NILMBENCH_REFIT`, and `NILMBENCH_UKDALE` to their full paths. Check the files before a long run:

```bash
nilmbench doctor --checksums
nilmbench validate --task corrected-t1-redd --check-data --max-samples 64
```

The dataset mounts in `compose.yaml` are read-only. Results are written to a separate `/results` mount.
New runs land under `results/candidates`; publication is an explicit review/copy
into `results/published`, never an automatic side effect of training.

## CPU smoke and CUDA benchmark

Container builds take nilmtk-contrib as a named BuildKit context. The default Compose configuration expects the two repositories to be sibling directories; set `NILMTK_CONTRIB_CONTEXT` to override that location.

Published images pin their nilmtk-contrib build context to an exact reviewed
integration commit rather than a moving branch. The installable `benchmark`
extra currently pins
[`1148e1c65f43878dfa1b8e08dc6411f5991d7dbd`](https://github.com/nilmtk/nilmtk-contrib/commit/1148e1c65f43878dfa1b8e08dc6411f5991d7dbd);
the source revision and image digest for each verified runtime are recorded in
[`configs/runtimes.toml`](configs/runtimes.toml). Update either pin only when a
reviewed integration is adopted. Both image variants synchronize their runtime,
NILMTK, and NILM Metadata dependencies from the checked-in `uv.lock` with
`--frozen`; the project and named-context contrib source are then installed with
`--no-deps`. The CPU-only Torch wheel is installed with `--no-deps` after its
common Python dependencies have been synchronized from the same lock, avoiding
the CUDA wheel stack in the CPU image.

Model contributions and benchmark-image releases have separate cadences. A
model can merge after its contrib contract, CPU, and targeted CUDA checks pass;
it does not trigger a public image by itself. NILMbench periodically batches
eligible contrib changes, advances its single immutable contrib pin, builds the
matching `-cpu` and `-cuda` variants once, and runs the real-data matrix against
the candidate CUDA digest. The versioned images and leaderboard update are
promoted together only after that matrix passes. Development builds may use a
local contrib checkout, but their results cannot become verified leaderboard
rows unless the source revisions and immutable container digest are recorded.
For an official run, the orchestrator obtains the candidate image's registry or
local content digest and supplies it as `NILMBENCH_IMAGE_DIGEST`. The exact
runner commit, contrib commit, image name/digest, and hardware must then be
reviewed into `configs/runtimes.toml`; self-asserted environment variables alone
cannot produce a verified row.

For local Compose builds, pass the two source revisions into the OCI labels and
runtime result metadata:

```bash
export NILMBENCH_GIT_SHA="$(git rev-parse HEAD)"
export NILMTK_CONTRIB_GIT_SHA="$(git -C ../nilmtk-contrib rev-parse HEAD)"
```

The CPU path is deliberately small and is suitable for CI or a laptop:

```bash
export NILMBENCH_DATA_DIR=/absolute/path/to/data
export NILMBENCH_RESULTS_DIR=/absolute/path/to/results
docker compose run --rm cpu-smoke
```

The real benchmark path uses the pinned PyTorch 2.6.0 / CUDA 12.4 image and all visible NVIDIA GPUs:

```bash
docker compose --profile cuda run --rm cuda-benchmark
```

To validate one A100 execution path before the full study:

```bash
docker compose --profile cuda run --rm cuda-benchmark \
  run --task corrected-t1-redd --model PatchTST --appliance fridge \
  --seed 42 --epochs 1 --max-samples 1024 --device cuda --results /results/candidates
```

For the full paper matrix, repeat each task at 60 and 900 seconds for evaluation seeds 10, 20, and 42. Optuna studies live under `results/optuna/` and resume to the requested total trial count. Model selection uses the fixed tuning seed 42 once per scientific study, then freezes the selected parameters for every evaluation seed; independently tuned seeds are never pooled into one score. Trials are scored only on a blocked 20% holdout from each `task.train` window; `task.test` is loaded only after model selection, during final benchmark evaluation. Fixed epoch and sequence-length overrides apply during every trial. Study identity covers the runner and nilmtk-contrib revisions, container digest, device/runtime, source dataset identity, full task protocol, appliance subset, resolution, and smoke overrides, so an incompatible environment creates a new study instead of resuming an old one. Persistent HPO also fails closed for unknown or dirty source/container provenance.

## Historical versus corrected protocols

The recovered notebooks requested REDD building 1 from 1–30 April 2011, although this converted file starts on 18 April. `historical-t1-redd` retains that request and emits a coverage warning. Two other historical definitions request unavailable appliance/building pairs; validation reports those explicitly. Historical tasks also retain the legacy joint appliance alignment and fixed 10 W F1 threshold.

The eight `corrected-*` tasks form a real-data-validated T1/T2/T3 matrix. They enforce dataset/meter coverage, use the paper's appliance-specific F1 thresholds, select active or apparent power from an explicit preference list, and align each appliance independently. NILM Metadata supplies the canonical taxonomy and synonyms. Shared physical circuits are warned and recorded; a future clean-meter profile can reject them. Corrected profiles are the basis for new leaderboard claims, while historical profiles are retained for forensic reproduction.

## Repository layout

```text
configs/                 # dataset manifests and T1/T2/T3 task definitions
src/nilmbench/           # CLI, data loading, registry, runner, provenance
docker/                  # separate CPU-only and CUDA 12.4 images
tests/                   # dependency-light runner/config tests
index.html               # short chooser: paper, leaderboard, or runner
paper.html               # fixed NILMBench2026 paper website
leaderboard.html         # living board generated from leaderboard.json
static/site.css          # shared landing/leaderboard styles
static/leaderboard.js    # cohort-aware leaderboard renderer
static/images/           # paper figures
```

The website still has no build step:

```bash
python3 -m http.server 8000
```

## Living leaderboard

The dedicated `leaderboard.html` page reads a generated table from immutable
`result.json` bundles under `results/published`; its numbers are never edited
into the website. Regenerate
both reviewable artifacts after adding a result bundle:

```bash
nilmbench leaderboard --results results/published \
  --output leaderboard.json --csv leaderboard.csv
git diff -- leaderboard.json leaderboard.csv
```

Those immutable, hashable JSON bundles are the primary scientific artifacts.
Each completed HPO trial also has a write-once JSON audit record under its
`results/optuna/<study>/trials/` directory, and those records are embedded in
the final result bundle. SQLite is reserved for mutable coordination such as resumable Optuna studies;
an optional SQLite query index may be generated later, but it must always be
rebuildable from the result bundles and never replace them.
The CSV is written before the JSON commit marker; the JSON records the CSV's
SHA-256 so consumers can reject a partially updated artifact pair.

Every aggregate is separated by task/config revision, model revision, runner
revision, container digest, hardware, resolution, appliance, target-data access,
smoke/full scope, and a digest of every protocol override. Context length,
epochs, sample limits, tuning records, and runtime provenance remain visible in
the generated artifacts.

Public rank is generated once, in Python, within an evaluation cohort defined by
task/config revision, profile, resolution, appliance, sample limit, target-data
access, and smoke/full scope. Architecture choices such as context length and
epoch count, and provenance changes such as a later container build, do not
reset rank. The complete comparison-protocol digest still records those details
for audit and exact reproduction. A corrected
full run becomes `full-verified`, and a smoke run becomes `smoke-verified`, only
after the required seeds 10, 20, and 42 pass source, container, and dataset
provenance checks. Incomplete clean smoke matrices are labelled `smoke-partial`.
CI regenerates the artifacts and rejects hand-edited or stale tables.

## Add a model

Models belong in nilmtk-contrib. Once a model has its own tests and lazy export there, add a small entry and search space in `src/nilmbench/registry.py`; task/data logic should not be copied into model notebooks. PatchTST, ModernTCN, DLinear, TimesNet, SGN, TSMixer, NILMMoE, ResidualMoE, and HSMM use this route. Non-neural models such as HSMM record their scientific settings as fixed registry parameters instead of pretending to have neural training epochs or a sequence length.

## Cite

```bibtex
@inproceedings{kuloor2026nilmbench,
  title     = {NILMBench2026: A Benchmark for Energy Disaggregation},
  author    = {Kuloor, Aayush and Singh, Anurag and Dhru, Harsh and Batra, Nipun},
  booktitle = {Proceedings of the 13th ACM International Conference on Systems for
               Energy-Efficient Buildings, Cities, and Transportation (BuildSys '26)},
  year      = {2026},
  doi       = {10.1145/3744256.3812587},
  publisher = {ACM},
  address   = {Banff, AB, Canada}
}
```
