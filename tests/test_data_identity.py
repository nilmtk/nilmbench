import hashlib

import pytest

from nilmbench.config import DatasetConfig
from nilmbench.data import DataError, verify_dataset


def _dataset(path, *, size=None, sha256=None):
    content = path.read_bytes()
    return DatasetConfig(
        id="TEST",
        path_env="NILMBENCH_TEST_UNUSED",
        default_path=str(path),
        sha256=sha256 or hashlib.sha256(content).hexdigest(),
        size_bytes=len(content) if size is None else size,
        timezone="UTC",
        mains_ac_types=("active",),
        appliance_ac_types=("active",),
    )


def test_dataset_identity_records_verified_file(tmp_path):
    path = tmp_path / "dataset.h5"
    path.write_bytes(b"real benchmark bytes")

    identity = verify_dataset(_dataset(path))

    assert identity.path == str(path.resolve())
    assert identity.size_bytes == path.stat().st_size
    assert identity.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_dataset_identity_rejects_wrong_size_before_training(tmp_path):
    path = tmp_path / "dataset.h5"
    path.write_bytes(b"wrong size")

    with pytest.raises(DataError, match="bytes"):
        verify_dataset(_dataset(path, size=999))


def test_dataset_identity_rejects_wrong_checksum_before_training(tmp_path):
    path = tmp_path / "dataset.h5"
    path.write_bytes(b"wrong checksum")

    with pytest.raises(DataError, match="SHA-256"):
        verify_dataset(_dataset(path, sha256="0" * 64))
