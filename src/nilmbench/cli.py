"""Command-line interface for reproducible NILMbench runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from nilmbench.config import ConfigError, load_config
from nilmbench.data import DataError, load_split
from nilmbench.provenance import runtime_provenance
from nilmbench.registry import MODELS
from nilmbench.runner import run_benchmark


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nilmbench")
    parser.add_argument("--config-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list configured tasks and models")
    doctor = sub.add_parser("doctor", help="show runtime and dataset availability")
    doctor.add_argument("--checksums", action="store_true")

    validate = sub.add_parser("validate", help="validate one task without training")
    validate.add_argument("--task", required=True)
    validate.add_argument("--check-data", action="store_true")

    run = sub.add_parser("run", help="run one model on one benchmark task")
    run.add_argument("--task", required=True)
    run.add_argument("--model", default="PatchTST", choices=sorted(MODELS))
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--trials", type=int, default=0)
    run.add_argument("--results", type=Path, default=Path("results"))
    run.add_argument("--appliance", action="append", dest="appliances")
    run.add_argument("--sample-period", type=int, choices=(60, 900))
    run.add_argument("--max-samples", type=int)
    run.add_argument("--epochs", type=int)
    run.add_argument("--device")
    run.add_argument("--dry-run", action="store_true")
    return parser


def _doctor(config: object, checksums: bool) -> int:
    root = Path(__file__).resolve().parents[2]
    payload = {"runtime": runtime_provenance(root), "datasets": {}}
    for name, dataset in config.datasets.items():
        path = dataset.path
        info = {
            "path": str(path),
            "exists": path.is_file(),
            "expected_size_bytes": dataset.size_bytes,
            "expected_sha256": dataset.sha256,
        }
        if path.is_file():
            info["size_bytes"] = path.stat().st_size
            info["size_matches"] = path.stat().st_size == dataset.size_bytes
            if checksums:
                info["sha256"] = _sha256(path)
                info["checksum_matches"] = info["sha256"] == dataset.sha256
        payload["datasets"][name] = info
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config_dir)
        if args.command == "list":
            for task in config.tasks.values():
                print(f"{task.id}\t{task.family}\t{task.profile}\t{task.description}")
            print("models\t" + ", ".join(sorted(MODELS)))
            return 0
        if args.command == "doctor":
            return _doctor(config, args.checksums)
        if args.command == "validate":
            task = config.task(args.task)
            payload = {
                "task": asdict(task),
                "config_sha256": config.digest(task.id),
                "datasets": {
                    name: {
                        "path": str(config.datasets[name].path),
                        "exists": config.datasets[name].path.is_file(),
                    }
                    for name in sorted(
                        {w.dataset for w in (*task.train, *task.test)}
                    )
                },
            }
            if args.check_data and not all(
                item["exists"] for item in payload["datasets"].values()
            ):
                raise DataError("One or more configured dataset files are missing")
            if args.check_data:
                groups = (
                    [(name,) for name in task.appliances]
                    if task.alignment_policy == "per_appliance"
                    else [task.appliances]
                )
                observed = {}
                for group in groups:
                    label = group[0] if len(group) == 1 else "joint"
                    train = load_split(
                        config,
                        task,
                        task.train,
                        group,
                        task.sample_period,
                    )
                    test = load_split(
                        config,
                        task,
                        task.test,
                        group,
                        task.sample_period,
                    )
                    observed[label] = {
                        "train": train.metadata(),
                        "test": test.metadata(),
                    }
                payload["observed"] = observed
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            task = config.task(args.task)
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "task": asdict(task),
                            "config_sha256": config.digest(task.id),
                            "model": args.model,
                            "seed": args.seed,
                            "trials": args.trials,
                            "appliances": args.appliances or task.appliances,
                            "sample_period": args.sample_period or task.sample_period,
                            "max_samples": args.max_samples,
                            "epochs": args.epochs,
                            "device": args.device,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0
            output = run_benchmark(
                config,
                args.task,
                args.model,
                args.seed,
                args.results,
                trials=args.trials,
                appliances=tuple(args.appliances) if args.appliances else None,
                sample_period=args.sample_period,
                max_samples=args.max_samples,
                epochs=args.epochs,
                device=args.device,
            )
            print(output)
            return 0
    except (ConfigError, DataError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
