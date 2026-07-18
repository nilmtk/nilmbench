from nilmbench.config import TrustedRuntimeConfig, load_config


def test_builtin_config_has_complete_paper_task_matrix():
    config = load_config()
    assert config.trusted_runtimes == (
        TrustedRuntimeConfig(
            id="t0-redd-a100-83fb39e",
            nilmbench_git_sha="83fb39e57d50ac433314e880176fef187997d5b3",
            nilmtk_contrib_git_sha=(
                "825740b39bcd44b3f4bfaf146f4c0d944843b131"
            ),
            container_image="nilmbench:t0-83fb39e-cuda",
            container_digest=(
                "sha256:dd977962c2e0d72e2d923f8f5c3e92e538f67a00495e545c2f22571001872e91"
            ),
            hardware="NVIDIA A100-SXM4-80GB",
        ),
        TrustedRuntimeConfig(
            id="t0-redd-fridge-a100-adaf03e",
            nilmbench_git_sha="adaf03e42f5b8dd6b9ab95942ee191999f2d3b25",
            nilmtk_contrib_git_sha=(
                "8d745493ed9f84dd00fb502ffe85943eaeedc4c8"
            ),
            container_image="nilmbench:t0-adaf03e-cuda",
            container_digest=(
                "sha256:d7253754a6a9133235076fa1c1555104aa8be8128443da96bc16ae3d46809aa8"
            ),
            hardware="NVIDIA A100-SXM4-80GB",
        ),
        TrustedRuntimeConfig(
            id="t0-redd-fridge-a100-106af6b",
            nilmbench_git_sha="106af6b8d663d19e867d2026cf90f3053600140b",
            nilmtk_contrib_git_sha=(
                "c130293e24e16817b9859d1b78ae18bd988b1219"
            ),
            container_image="nilmbench:t0-106af6b-cuda",
            container_digest=(
                "sha256:4b233308c342556efd0be0d88f24586d2808cc9b7f86cde1d52cafccacb4c425"
            ),
            hardware="NVIDIA A100-SXM4-80GB",
        ),
    )
    historical = [
        task for task in config.tasks.values() if task.profile == "historical"
    ]
    corrected = [task for task in config.tasks.values() if task.profile == "corrected"]
    assert len(historical) == 8
    assert len(corrected) == 8
    assert {task.family for task in historical} == {"T1", "T2", "T3"}
    assert {task.alignment_policy for task in historical} == {"joint"}
    assert config.task("corrected-t1-redd").alignment_policy == "per_appliance"
    assert {task.alignment_policy for task in corrected} == {"per_appliance"}
    assert {task.metric_policy for task in historical} == {"legacy-nilmtk-10w"}
    assert {task.metric_policy for task in corrected} == {"paper-appliance-thresholds"}
    assert {task.shared_meter_policy for task in historical} == {"allow"}
    assert {task.shared_meter_policy for task in corrected} == {"warn"}
    assert {task.minimum_aligned_fraction for task in corrected} == {0.5}
    assert {task.target_data_access for task in corrected if task.family == "T3"} == {
        "none"
    }
    assert {task.target_data_access for task in corrected if task.family != "T3"} == {
        "not_applicable"
    }


def test_corrected_redd_t2_matches_paper_building_split():
    task = load_config().task("corrected-t2-redd")
    assert [window.building for window in task.train] == [1, 2, 3]
    assert [window.building for window in task.test] == [6]
    assert task.appliances == ("fridge", "washing machine", "dish washer")


def test_metric_policies_make_threshold_difference_explicit():
    config = load_config()
    legacy = config.metric_policy("legacy-nilmtk-10w")
    paper = config.metric_policy("paper-appliance-thresholds")
    assert legacy.threshold("microwave") == 10.0
    assert paper.threshold("fridge") == 50.0
    assert paper.threshold("microwave") == 200.0
    assert paper.threshold("kettle") == 2000.0


def test_config_digest_is_stable():
    config = load_config()
    first = config.digest("historical-t1-redd")
    second = load_config().digest("historical-t1-redd")
    assert first == second
    assert len(first) == 64
