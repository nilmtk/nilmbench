# NILMbench

Reproducible runner and project website for **[NILMBench2026](https://sustainability-lab.github.io/nilmbench/)** — *A Benchmark for Energy Disaggregation* (BuildSys '26, **Best Paper Candidate**).

> One aggregate power signal in. Appliance-level estimates out. We benchmark **16 NILM models**
> across **3 datasets** and **2 resolutions** — on accuracy, efficiency, and generalization —
> and find that **generalization is the wall**.

**Authors:** Aayush Kuloor\*, Anurag Singh\*, Harsh Dhru\*, Nipun Batra† · IIT Gandhinagar
(\* equal contribution, † corresponding author)

## Links

- 📄 **Paper:** https://sustainability-lab.github.io/papers/2026/nilmbench2026_buildsys.pdf
- 💻 **Code (modernized NILMTK):** https://github.com/nilmtk/nilmtk-contrib
- 🌐 **Website:** https://sustainability-lab.github.io/nilmbench/

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

The REDD, UK-DALE, and REFIT data are not redistributed. The runner expects user-provided NILMTK HDF5 conversions and verifies them against the recorded file sizes and SHA-256 digests. The exact protocol discrepancies recovered from the old notebooks are documented in [`docs/protocol-audit.md`](docs/protocol-audit.md).

## Install for development

Use Python 3.11, which is the version currently supported by nilmtk-contrib:

```bash
git clone https://github.com/nilmtk/nilmtk-contrib.git
git clone https://github.com/sustainability-lab/nilmbench.git
cd nilmbench

uv venv --python 3.11
source .venv/bin/activate
UV_TORCH_BACKEND=cpu uv pip install -e "../nilmtk-contrib[torch]" -e ".[runtime,dev]"
nilmbench list
pytest
```

For a non-editable install after the PatchTST nilmtk-contrib release is available:

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

## CPU smoke and CUDA benchmark

Container builds take nilmtk-contrib as a named BuildKit context. The default Compose configuration expects the two repositories to be sibling directories; set `NILMTK_CONTRIB_CONTEXT` to override that location.

Published images pin their nilmtk-contrib build context to the exact PatchTST commit rather than a moving branch. Update that pin deliberately when a reviewed model release is adopted.

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

To run one inspectable A100 smoke before spending on 20 trials:

```bash
docker compose --profile cuda run --rm cuda-benchmark \
  run --task corrected-t1-redd --model PatchTST --appliance fridge \
  --seed 42 --epochs 1 --max-samples 1024 --device cuda --results /results
```

For the full paper matrix, repeat each task at 60 and 900 seconds for seeds 10, 20, and 42. Optuna studies live under `results/optuna/` and resume to the requested total trial count. Their identity hashes the task config, model, seed, appliance subset, resolution, and smoke overrides, preventing incompatible runs from sharing a study.

## Historical versus corrected protocols

The recovered notebooks requested REDD building 1 from 1–30 April 2011, although this converted file starts on 18 April. `historical-t1-redd` retains that request and emits a coverage warning. Two other historical definitions request unavailable appliance/building pairs; validation reports those explicitly. Historical tasks also retain the legacy joint appliance alignment and fixed 10 W F1 threshold.

The eight `corrected-*` tasks form a real-data-validated T1/T2/T3 matrix. They enforce dataset/meter coverage, use the paper's appliance-specific F1 thresholds, select active or apparent power from an explicit preference list, and align each appliance independently. NILM Metadata remains the source of truth for taxonomy and synonyms. Shared physical circuits are warned and recorded; a future clean-meter profile can reject them. Corrected profiles are the basis for new leaderboard claims, while historical profiles are retained for forensic reproduction.

## Repository layout

```text
configs/                 # dataset manifests and T1/T2/T3 task definitions
src/nilmbench/           # CLI, data loading, registry, runner, provenance
docker/                  # separate CPU-only and CUDA 12.4 images
tests/                   # dependency-light runner/config tests
index.html               # self-contained project website
static/images/           # paper figures
```

The website still has no build step:

```bash
python3 -m http.server 8000
```

## Add a model

Models belong in nilmtk-contrib. Once a model has its own tests and lazy export there, add a small entry and search space in `src/nilmbench/registry.py`; task/data logic should not be copied into model notebooks. PatchTST is the first model using this route.

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
