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


def test_config_digest_is_stable():
    config = load_config()
    first = config.digest("historical-t1-redd")
    second = load_config().digest("historical-t1-redd")
    assert first == second
    assert len(first) == 64
