import json
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from nilmbench import runner
from nilmbench.data import DatasetIdentity, LoadedSplit
from nilmbench.config import load_config
from nilmbench.registry import get_model
from nilmbench.runner import (
    _assert_resume_compatible,
    _load_completed_trial_records,
    _model_size,
    _predict,
    _run_validation_once,
    _study_identity,
    _study_spec,
    _trial_parameters,
    _write_trial_record,
    run_benchmark,
)


class _Model:
    def __init__(self, chunks):
        self.chunks = chunks

    def disaggregate_chunk(self, mains):
        del mains
        return self.chunks


class _Parameter:
    def __init__(self, count):
        self.count = count

    def numel(self):
        return self.count


class _Network:
    def __init__(self, *parameters):
        self._parameters = parameters

    def parameters(self):
        return iter(self._parameters)


def _split():
    return LoadedSplit(
        mains=[pd.DataFrame({"mains": [1.0, 2.0]}), pd.DataFrame({"mains": [3.0]})],
        appliances={},
        windows=[],
    )


def test_predict_preserves_final_partial_chunk():
    split = _split()
    model = _Model(
        [
            pd.DataFrame({"fridge": [1.0, 2.0]}, index=split.mains[0].index),
            pd.DataFrame({"fridge": [3.0]}, index=split.mains[1].index),
        ]
    )

    prediction = _predict(model, split, ("fridge",))

    assert prediction["fridge"].tolist() == [1.0, 2.0, 3.0]


def test_model_size_includes_auxiliary_networks_without_double_counting():
    shared = _Parameter(10)
    main_only = _Parameter(20)
    attention_only = _Parameter(30)
    model = type(
        "Model",
        (),
        {
            "models": {"fridge": _Network(shared, main_only)},
            "att_models": {"fridge": _Network(shared, attention_only)},
        },
    )()

    assert _model_size(model) == 60


def test_non_neural_baseline_has_no_trainable_parameter_count():
    assert _model_size(type("Baseline", (), {"model": {"fridge": 42.0}})()) is None


def test_non_neural_fitted_records_in_models_have_no_parameter_count():
    fitted_record = SimpleNamespace(state_means=(0.0, 100.0))
    model = type("StateSpaceModel", (), {"models": {"fridge": fitted_record}})()

    assert _model_size(model) is None


def test_model_size_counts_networks_and_ignores_non_neural_records():
    model = type(
        "HybridModel",
        (),
        {
            "models": {
                "fridge": _Network(_Parameter(17)),
                "duration_prior": SimpleNamespace(max_duration=720),
            }
        },
    )()

    assert _model_size(model) == 17


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        ([pd.DataFrame({"fridge": [1.0, 2.0]})], "number of prediction chunks"),
        (
            [
                pd.DataFrame({"fridge": [1.0]}),
                pd.DataFrame({"fridge": [3.0]}),
            ],
            "rows",
        ),
        (
            [
                pd.DataFrame({"wrong": [1.0, 2.0]}),
                pd.DataFrame({"wrong": [3.0]}),
            ],
            "columns",
        ),
        (
            [
                pd.DataFrame({"fridge": [1.0, float("nan")]}),
                pd.DataFrame({"fridge": [3.0]}),
            ],
            "non-finite",
        ),
        (
            [
                pd.DataFrame({"fridge": [1.0, 2.0]}, index=[1, 0]),
                pd.DataFrame({"fridge": [3.0]}),
            ],
            "index",
        ),
    ],
)
def test_predict_rejects_dropped_or_corrupt_outputs(chunks, message):
    with pytest.raises(RuntimeError, match=message):
        _predict(_Model(chunks), _split(), ("fridge",))


@pytest.mark.parametrize("sequence_length", [False, 0, 1.5, "299"])
def test_run_rejects_invalid_sequence_length_before_data_access(
    sequence_length, tmp_path
):
    with pytest.raises(ValueError, match="positive integer"):
        run_benchmark(
            load_config(),
            "corrected-t1-redd",
            "PatchTST",
            42,
            tmp_path,
            sequence_length=sequence_length,
        )


@pytest.mark.parametrize(
    ("model_name", "overrides"),
    [
        (model_name, overrides)
        for model_name in ("HSMM", "Mean")
        for overrides in (
            {"trials": 1},
            {"epochs": 1},
            {"sequence_length": 99},
            {"device": "cpu"},
        )
    ],
)
def test_non_neural_models_reject_irrelevant_overrides_before_data_access(
    model_name, overrides, tmp_path
):
    with pytest.raises(ValueError, match="does not accept"):
        run_benchmark(
            load_config(),
            "corrected-t1-redd",
            model_name,
            42,
            tmp_path,
            **overrides,
        )


def test_hsmm_fixed_parameters_are_passed_and_recorded(monkeypatch, tmp_path):
    captured = []

    def fake_verify(dataset):
        return DatasetIdentity(
            id=dataset.id,
            path=f"/data/{dataset.id}.h5",
            size_bytes=dataset.size_bytes,
            sha256=dataset.sha256,
        )

    def fake_run(*args, **kwargs):
        del kwargs
        captured.append(args[4])
        return {
            "metrics": {
                "fridge": {
                    "mae": 1.0,
                    "f1": 0.5,
                    "activation_threshold_watts": 50.0,
                }
            },
            "objective_mae": 1.0,
        }

    monkeypatch.setattr(runner, "verify_dataset", fake_verify)
    monkeypatch.setattr(runner, "runtime_provenance", lambda root: _provenance())
    monkeypatch.setattr(runner, "_run_once", fake_run)

    output = run_benchmark(
        load_config(),
        "corrected-t1-redd",
        "HSMM",
        42,
        tmp_path,
        appliances=("fridge",),
        max_samples=3960,
    )

    expected = dict(get_model("HSMM").fixed_params)
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert captured == [expected]
    assert result["model_params"] == expected
    assert result["protocol_overrides"]["epochs"] is None
    assert result["protocol_overrides"]["sequence_length"] is None


class _ValidationModel:
    fitted_params = None

    def __init__(self, params):
        type(self).fitted_params = params

    def partial_fit(self, mains, appliances):
        assert all(len(frame) == 8 for frame in mains)
        assert [name for name, _ in appliances] == ["fridge"]

    def disaggregate_chunk(self, mains):
        return [
            pd.DataFrame({"fridge": [0.0] * len(frame)}, index=frame.index)
            for frame in mains
        ]


def test_hpo_validation_cannot_access_task_test_or_target_domain(monkeypatch):
    config = load_config()
    task = config.task("corrected-t3-redd-to-refit")
    source_index = pd.date_range("2011-04-18", periods=10, freq="min")
    source = LoadedSplit(
        mains=[pd.DataFrame({"mains": range(10)}, index=source_index)],
        appliances={
            "fridge": [pd.DataFrame({"fridge": range(10)}, index=source_index)]
        },
        windows=[],
    )
    accessed = []

    def guarded_load_split(
        config_arg,
        task_arg,
        windows,
        appliances,
        sample_period,
        max_samples,
    ):
        del config_arg, appliances, sample_period, max_samples
        assert task_arg is task
        requested = tuple(windows)
        accessed.append(requested)
        assert requested == task.train
        assert {window.dataset for window in requested} == {"REDD"}
        assert requested != task.test
        return source

    monkeypatch.setattr(runner, "load_split", guarded_load_split)
    monkeypatch.setattr(runner, "_configure_determinism", lambda seed: None)
    monkeypatch.setattr(
        runner,
        "get_model",
        lambda name: SimpleNamespace(model_class=lambda: _ValidationModel),
    )

    result = _run_validation_once(
        config,
        task,
        "AdversarialFake",
        42,
        {"sequence_length": 123},
        ("fridge",),
        60,
        None,
    )

    assert accessed == [task.train]
    assert result["validation_protocol"]["task_test_access"] == "forbidden"
    assert result["validation_partitions"]["fridge"][0]["training"]["samples"] == 8
    assert result["validation_partitions"]["fridge"][0]["validation"]["samples"] == 2
    assert _ValidationModel.fitted_params["sequence_length"] == 123


class _HostileTrial:
    def __init__(self):
        self.requested = []

    def suggest_categorical(self, name, choices):
        self.requested.append(name)
        return tuple(choices)[-1]

    def suggest_int(self, name, low, high, **kwargs):
        del low, kwargs
        self.requested.append(name)
        return high

    def suggest_float(self, name, low, high, **kwargs):
        del high, kwargs
        self.requested.append(name)
        return low


def test_fixed_sequence_length_and_epochs_are_honored_inside_every_trial():
    trial = _HostileTrial()

    params = _trial_parameters(
        get_model("PatchTST"),
        trial,
        epochs=3,
        sequence_length=123,
        device="cpu",
    )

    assert params["sequence_length"] == 123
    assert params["n_epochs"] == 3
    assert params["device"] == "cpu"
    assert "sequence_length" not in trial.requested
    assert "n_epochs" not in trial.requested
    assert set(trial.requested) == {"batch_size", "learning_rate"}


def _provenance():
    return {
        "nilmbench_git_sha": "a" * 40,
        "nilmbench_git_dirty": False,
        "nilmtk_contrib_git_sha": "b" * 40,
        "nilmtk_contrib_git_dirty": False,
        "nilmtk_contrib_version": "1.0",
        "container_image": "nilmbench:cuda",
        "container_digest": "sha256:" + "c" * 64,
        "cpu": "cpu-a",
        "gpu": "gpu-a",
        "torch": "2.6.0",
        "cuda_runtime": "12.4",
        "cuda_available": True,
    }


def test_result_records_post_run_determinism_provenance(monkeypatch, tmp_path):
    snapshots = [
        _provenance() | {"deterministic_algorithms": False},
        _provenance() | {"deterministic_algorithms": True},
    ]

    def fake_verify(dataset):
        return DatasetIdentity(
            id=dataset.id,
            path=f"/data/{dataset.id}.h5",
            size_bytes=dataset.size_bytes,
            sha256=dataset.sha256,
        )

    def fake_run(*args, **kwargs):
        del args, kwargs
        return {
            "metrics": {
                "fridge": {
                    "mae": 1.0,
                    "f1": 0.5,
                    "activation_threshold_watts": 50.0,
                }
            },
            "objective_mae": 1.0,
        }

    monkeypatch.setattr(runner, "verify_dataset", fake_verify)
    monkeypatch.setattr(runner, "runtime_provenance", lambda root: snapshots.pop(0))
    monkeypatch.setattr(runner, "_run_once", fake_run)

    output = run_benchmark(
        load_config(),
        "corrected-t1-redd",
        "HSMM",
        42,
        tmp_path,
        appliances=("fridge",),
        max_samples=3960,
    )

    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["runtime"]["deterministic_algorithms"] is True
    assert snapshots == []


def _complete_study_spec():
    config = load_config()
    task = config.task("corrected-t3-redd-to-refit")
    return _study_spec(
        config,
        task,
        "PatchTST",
        get_model("PatchTST"),
        42,
        ("fridge",),
        60,
        None,
        10,
        299,
        "cuda",
        _provenance(),
        {"REDD": {"sha256": "source-a", "size_bytes": 123}},
        "4.5.0",
    )


@pytest.mark.parametrize(
    ("section", "key", "replacement"),
    [
        ("runner", "git_sha", "runner-b"),
        ("contrib", "git_sha", "contrib-b"),
        ("container", "digest", "sha256:container-b"),
        ("device", "gpu", "gpu-b"),
        ("protocol", "sequence_length_override", 599),
    ],
)
def test_study_identity_changes_for_every_resume_boundary(section, key, replacement):
    original = _complete_study_spec()
    incompatible = deepcopy(original)
    incompatible[section][key] = replacement

    original_digest, original_name = _study_identity(
        "corrected-t3-redd-to-refit", "PatchTST", 42, original
    )
    changed_digest, changed_name = _study_identity(
        "corrected-t3-redd-to-refit", "PatchTST", 42, incompatible
    )

    assert original_digest != changed_digest
    assert original_name != changed_name


class _FakeStudy:
    def __init__(self, *, user_attrs=None, trials=None, study_name="study"):
        self.user_attrs = user_attrs or {}
        self.trials = trials or []
        self.study_name = study_name

    def set_user_attr(self, name, value):
        self.user_attrs[name] = value


def test_resume_rejects_incompatible_study_even_if_storage_name_is_reused():
    spec = _complete_study_spec()
    digest, _ = _study_identity("task", "PatchTST", 42, spec)
    study = _FakeStudy()
    _assert_resume_compatible(study, spec, digest)
    incompatible = deepcopy(spec)
    incompatible["container"]["digest"] = "sha256:hostile"

    with pytest.raises(RuntimeError, match="incompatible scientific identity"):
        _assert_resume_compatible(study, incompatible, digest)


def test_resume_rejects_completed_sqlite_trial_without_json_audit(tmp_path):
    complete = object()
    trial = SimpleNamespace(number=0, state=complete, params={}, value=1.0)
    study = _FakeStudy(trials=[trial], study_name="study")

    with pytest.raises(RuntimeError, match="no valid immutable JSON audit"):
        _load_completed_trial_records(study, complete, tmp_path, "digest", {})


def test_trial_json_audit_is_write_once(tmp_path):
    path = tmp_path / "trial-000000.json"
    _write_trial_record(path, {"record_id": "first"})
    _write_trial_record(path, {"record_id": "first"})

    with pytest.raises(RuntimeError, match="Refusing to replace immutable artifact"):
        _write_trial_record(path, {"record_id": "second"})

    assert path.read_text(encoding="utf-8") == '{\n  "record_id": "first"\n}\n'


def test_end_to_end_hpo_defers_target_dataset_until_final_evaluation(
    monkeypatch, tmp_path
):
    pytest.importorskip("optuna")
    config = load_config()
    events = []
    final_params = []

    def fake_verify(dataset):
        events.append(("verify", dataset.id))
        return DatasetIdentity(
            id=dataset.id,
            path=f"/data/{dataset.id}.h5",
            size_bytes=dataset.size_bytes,
            sha256=dataset.sha256,
        )

    def fake_space(trial):
        return {
            "sequence_length": trial.suggest_categorical("sequence_length", [99, 299]),
            "n_epochs": trial.suggest_int("n_epochs", 10, 20),
            "batch_size": trial.suggest_categorical("batch_size", [128]),
            "learning_rate": trial.suggest_float("learning_rate", 1e-4, 1e-3, log=True),
        }

    model_entry = SimpleNamespace(
        supports_training_overrides=True,
        module="fake.contrib",
        class_name="FakeModel",
        family="transformer",
        search_space=fake_space,
    )

    def fake_validation(*args, **kwargs):
        del kwargs
        events.append(("score", "source-validation"))
        effective = args[4]
        return {
            "params_by_alignment_group": {
                "fridge": {
                    **effective,
                    "seed": args[3],
                    "mains_mean": 1.0,
                    "mains_std": 1.0,
                }
            },
            "metrics": {
                "fridge": {
                    "mae": 1.0,
                    "f1": 0.0,
                    "activation_threshold_watts": 50.0,
                }
            },
            "objective_mae": 1.0,
            "validation_protocol": dict(runner._VALIDATION_PROTOCOL),
            "validation_partitions": {
                "fridge": [
                    {
                        "source_window": {
                            "samples": 10,
                            "actual_start": "2011-04-18T00:00:00",
                            "actual_end": "2011-04-18T00:09:00",
                        },
                        "training": {
                            "samples": 8,
                            "actual_start": "2011-04-18T00:00:00",
                            "actual_end": "2011-04-18T00:07:00",
                        },
                        "validation": {
                            "samples": 2,
                            "actual_start": "2011-04-18T00:08:00",
                            "actual_end": "2011-04-18T00:09:00",
                        },
                    }
                ]
            },
            "elapsed_seconds_by_alignment_group": {"fridge": 0.1},
            "elapsed_seconds": 0.1,
        }

    def fake_final(*args, **kwargs):
        del kwargs
        events.append(("score", "target-test"))
        final_params.append(args[4])
        return {
            "metrics": {
                "fridge": {
                    "mae": 2.0,
                    "f1": 0.0,
                    "activation_threshold_watts": 50.0,
                }
            },
            "objective_mae": 2.0,
        }

    monkeypatch.setattr(runner, "verify_dataset", fake_verify)
    monkeypatch.setattr(runner, "runtime_provenance", lambda root: _provenance())
    monkeypatch.setattr(runner, "get_model", lambda name: model_entry)
    monkeypatch.setattr(runner, "_run_validation_once", fake_validation)
    monkeypatch.setattr(runner, "_run_once", fake_final)

    output = run_benchmark(
        config,
        "corrected-t3-redd-to-refit",
        "PatchTST",
        42,
        tmp_path,
        trials=1,
        appliances=("fridge",),
        epochs=3,
        sequence_length=123,
        device="cpu",
    )

    validation_index = events.index(("score", "source-validation"))
    target_verify_index = events.index(("verify", "REFIT"))
    target_score_index = events.index(("score", "target-test"))
    assert validation_index < target_verify_index < target_score_index
    assert final_params[0]["sequence_length"] == 123
    assert final_params[0]["n_epochs"] == 3

    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["study"]["completed_trials"] == 1
    assert result["study"]["coordination"]["scientific_source_of_truth"] is False
    assert len(result["study"]["trial_records"]) == 1
    assert result["protocol_overrides"]["model_selection"] == {
        "method": "optuna-tpe",
        "selection_protocol": "tune-once-freeze-v1",
        "tuning_seed": 42,
        "study_identity_sha256": result["study"]["study_digest"],
        "completed_trials": 1,
        "validation_protocol": "source-train-blocked-holdout-v1",
        "selected_parameters": result["study"]["best_params"],
    }
    record = result["study"]["trial_records"][0]
    assert record["parameters"]["effective"]["sequence_length"] == 123
    assert set(record["study_spec"]["source_dataset_identities"]) == {"REDD"}
    assert record["parameters"]["resolved_by_alignment_group"]["fridge"]["seed"] == 42

    second_output = run_benchmark(
        config,
        "corrected-t3-redd-to-refit",
        "PatchTST",
        20,
        tmp_path,
        trials=1,
        appliances=("fridge",),
        epochs=3,
        sequence_length=123,
        device="cpu",
    )
    second_result = json.loads(
        (second_output / "result.json").read_text(encoding="utf-8")
    )
    assert events.count(("score", "source-validation")) == 1
    assert second_result["study"]["study_digest"] == result["study"]["study_digest"]
    assert second_result["model_params"] == result["model_params"]
    assert second_result["seed"] == 20

    audit_path = tmp_path / result["study"]["trial_record_files"][0]
    audit_text = audit_path.read_text(encoding="utf-8")
    audit_path.chmod(0o600)
    audit_path.write_text(
        audit_text.replace('"objective_mae": 1.0', '"objective_mae": 1e309'),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="no valid immutable JSON audit"):
        run_benchmark(
            config,
            "corrected-t3-redd-to-refit",
            "PatchTST",
            10,
            tmp_path,
            trials=1,
            appliances=("fridge",),
            epochs=3,
            sequence_length=123,
            device="cpu",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nilmbench_git_dirty", True),
        ("nilmtk_contrib_git_sha", None),
        ("container_digest", "unknown"),
        ("cpu", 7),
        ("cpu", ["hostile"]),
        ("cpu", {"hostile": True}),
        ("gpu", {"hostile": True}),
    ],
)
def test_persistent_hpo_rejects_dirty_or_unknown_provenance_before_data_access(
    monkeypatch, tmp_path, field, value
):
    provenance = _provenance()
    provenance[field] = value
    monkeypatch.setattr(runner, "runtime_provenance", lambda root: provenance)
    monkeypatch.setattr(
        runner,
        "verify_dataset",
        lambda dataset: pytest.fail(f"accessed dataset {dataset.id}"),
    )

    with pytest.raises(ValueError, match="clean, immutable, known provenance"):
        run_benchmark(
            load_config(),
            "corrected-t3-redd-to-refit",
            "PatchTST",
            10,
            tmp_path,
            trials=1,
            appliances=("fridge",),
        )

    assert not (tmp_path / "optuna").exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("sample_period", True),
        ("max_samples", True),
        ("epochs", True),
        ("trials", True),
    ],
)
def test_boolean_run_budgets_are_rejected_before_data_access(name, value, tmp_path):
    with pytest.raises(ValueError, match="integer"):
        run_benchmark(
            load_config(),
            "corrected-t1-redd",
            "PatchTST",
            42,
            tmp_path,
            **{name: value},
        )
