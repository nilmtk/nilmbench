from nilmbench.registry import MODELS, get_model


def test_registry_exposes_all_smoke_tested_contrib_models():
    assert set(MODELS) == {
        "BERT",
        "ConvLSTM",
        "DAE",
        "MSDC",
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
        "TCN",
        "WindowGRU",
    }


def test_every_registry_entry_has_traceable_identity():
    for name, entry in MODELS.items():
        assert entry.name == name
        assert entry.module == "nilmtk_contrib.torch"
        assert entry.class_name
        assert entry.family
        assert get_model(name) is entry
