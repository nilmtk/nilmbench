from nilmbench.registry import MODELS, get_model


def test_registry_exposes_baseline_and_all_smoke_tested_contrib_models():
    assert set(MODELS) == {
        "BERT",
        "ConvLSTM",
        "DAE",
        "DLinear",
        "MSDC",
        "ModernTCN",
        "NILMFormer",
        "PatchTST",
        "Reformer",
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
    assert MODELS["Mean"].search_space(object()) == {}
    assert MODELS["TSMixer"].family == "mlp-mixer"
    for name, entry in MODELS.items():
        if name != "Mean":
            assert entry.module == "nilmtk_contrib.torch"
            assert entry.supports_training_overrides
