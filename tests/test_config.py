from nilmbench.config import load_config


def test_builtin_config_has_complete_paper_task_matrix():
    config = load_config()
    historical = [task for task in config.tasks.values() if task.profile == "historical"]
    corrected = [task for task in config.tasks.values() if task.profile == "corrected"]
    assert len(historical) == 8
    assert len(corrected) == 8
    assert {task.family for task in historical} == {"T1", "T2", "T3"}
    assert {task.alignment_policy for task in historical} == {"joint"}
    assert config.task("corrected-t1-redd").alignment_policy == "per_appliance"
    assert {task.alignment_policy for task in corrected} == {"per_appliance"}
    assert {task.metric_policy for task in historical} == {"legacy-nilmtk-10w"}
    assert {task.metric_policy for task in corrected} == {
        "paper-appliance-thresholds"
    }
    assert {task.shared_meter_policy for task in historical} == {"allow"}
    assert {task.shared_meter_policy for task in corrected} == {"warn"}
    assert {
        task.target_data_access for task in corrected if task.family == "T3"
    } == {"none"}
    assert {
        task.target_data_access for task in corrected if task.family != "T3"
    } == {"not_applicable"}


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
