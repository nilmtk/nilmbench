from nilmbench.registry import MODELS, get_model


def test_registry_exposes_baseline_and_all_smoke_tested_contrib_models():
    assert set(MODELS) == {
        "BERT",
        "ConvLSTM",
        "DAE",
        "DLinear",
        "FeatureMLP",
        "HSMM",
        "MSDC",
        "ModernTCN",
        "NILMFormer",
        "NILMMoE",
        "PatchTST",
        "Reformer",
        "ResidualMoE",
        "ResNet",
        "ResNetClassification",
        "RNN",
        "RNNAttention",
        "RNNAttentionClassification",
        "Seq2Point",
        "Seq2Seq",
        "SGN",
        "TCN",
        "TSMixer",
        "TimesNet",
        "WindowGRU",
        "Mean",
    }


def test_every_registry_entry_has_traceable_identity():
    for name, entry in MODELS.items():
        assert entry.name == name
        assert entry.class_name
        assert entry.family
        assert get_model(name) is entry

    assert MODELS["Mean"].module == "nilmtk.disaggregate"
    assert MODELS["Mean"].family == "statistical-baseline"
    assert not MODELS["Mean"].supports_training_overrides
    assert not MODELS["Mean"].requires_trainable_parameters
    assert not MODELS["Mean"].requires_accelerator_memory
    assert MODELS["Mean"].search_space(object()) == {}
    assert MODELS["TSMixer"].family == "mlp-mixer"
    assert MODELS["FeatureMLP"].family == "statistical-feature-mlp"
    assert MODELS["NILMMoE"].family == "mixture-of-experts"
    assert MODELS["ResidualMoE"].family == "residual-mixture-of-experts"
    assert MODELS["HSMM"].family == "explicit-duration"
    assert not MODELS["HSMM"].supports_training_overrides
    assert not MODELS["HSMM"].requires_trainable_parameters
    assert MODELS["HSMM"].requires_accelerator_memory
    assert dict(MODELS["HSMM"].fixed_params) == {
        "num_states": 2,
        "max_duration": 180,
        "pseudocount": 1.0,
        "variance_floor": 1.0,
        "kmeans_max_iterations": 100,
    }
    for name, entry in MODELS.items():
        if name != "Mean":
            assert entry.module == "nilmtk_contrib.torch"
        if name not in {"HSMM", "Mean"}:
            assert entry.supports_training_overrides
            assert entry.requires_trainable_parameters
            assert entry.requires_accelerator_memory
