"""Read-only NILMTK dataset loading with auditable window metadata."""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from nilmbench.config import BenchmarkConfig, TaskConfig, WindowConfig


class DataError(RuntimeError):
    """Raised when real benchmark data cannot satisfy a task."""


@dataclass(frozen=True)
class LoadedWindow:
    requested: WindowConfig
    available_start: str
    available_end: str
    actual_start: str
    actual_end: str
    samples: int
    expected_samples: int
    aligned_sample_fraction: float


@dataclass
class LoadedSplit:
    mains: list[Any]
    appliances: dict[str, list[Any]]
    windows: list[LoadedWindow]

    def metadata(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.windows]


def _first_column(frame: Any) -> Any:
    if frame is None or frame.empty:
        raise DataError("NILMTK returned no samples for a requested series")
    return frame[[frame.columns[0]]]


def _load_one(
    dataset_path: Path,
    window: WindowConfig,
    appliances: tuple[str, ...],
    sample_period: int,
    max_samples: int | None,
    mains_ac_type: str,
    appliance_ac_type: str,
) -> tuple[Any, dict[str, Any], LoadedWindow]:
    try:
        from nilmtk import DataSet
    except ModuleNotFoundError as exc:
        raise DataError(
            "Real-data runs require the benchmark extra: install nilmbench[benchmark]"
        ) from exc

    dataset = DataSet(str(dataset_path))
    try:
        if window.building not in dataset.buildings:
            raise DataError(
                f"{window.dataset} has no building {window.building} in {dataset_path}"
            )
        elec = dataset.buildings[window.building].elec
        selected_meters = [elec.mains()]
        for appliance in appliances:
            try:
                meter = elec.select_using_appliances(type=appliance)
            except KeyError:
                meter = None
            if meter is None or not meter.meters:
                available = sorted(
                    {
                        item.identifier.type
                        for meter in elec.submeters().meters
                        for item in meter.appliances
                    }
                )
                raise DataError(
                    f"{window.dataset} building {window.building} lacks {appliance!r}; "
                    f"available types: {', '.join(available)}"
                )
            selected_meters.append(meter)
        timeframes = [meter.get_timeframe() for meter in selected_meters]
        available_start = max(frame.start for frame in timeframes)
        available_end = min(frame.end for frame in timeframes)

        dataset.set_window(start=window.start, end=window.end)
        mains = _first_column(
            next(
                elec.mains().load(
                    physical_quantity="power",
                    ac_type=mains_ac_type,
                    sample_period=sample_period,
                )
            )
        )
        readings: dict[str, Any] = {}
        for appliance, meter in zip(appliances, selected_meters[1:], strict=True):
            readings[appliance] = _first_column(
                next(
                    meter.load(
                        physical_quantity="power",
                        ac_type=appliance_ac_type,
                        sample_period=sample_period,
                    )
                )
            )

        index = mains.dropna().index
        for frame in readings.values():
            index = index.intersection(frame.dropna().index)
        if max_samples is not None:
            index = index[:max_samples]
        if index.empty:
            raise DataError(
                f"No aligned samples for {window.dataset} building {window.building}"
            )
        mains = mains.loc[index]
        readings = {name: frame.loc[index] for name, frame in readings.items()}
        requested_seconds = (
            datetime.fromisoformat(window.end) - datetime.fromisoformat(window.start)
        ).total_seconds()
        expected_samples = max(1, int(requested_seconds // sample_period))
        loaded = LoadedWindow(
            requested=window,
            available_start=available_start.tz_localize(None).isoformat(),
            available_end=available_end.tz_localize(None).isoformat(),
            actual_start=index[0].isoformat(),
            actual_end=index[-1].isoformat(),
            samples=len(index),
            expected_samples=expected_samples,
            aligned_sample_fraction=len(index) / expected_samples,
        )
        return mains, readings, loaded
    finally:
        store = getattr(dataset, "store", None)
        if store is not None:
            store.close()


def load_split(
    config: BenchmarkConfig,
    task: TaskConfig,
    windows: Iterable[WindowConfig],
    appliances: tuple[str, ...],
    sample_period: int,
    max_samples: int | None = None,
) -> LoadedSplit:
    """Load and align all requested mains/appliance windows without writing data."""
    mains: list[Any] = []
    appliance_frames = {name: [] for name in appliances}
    loaded_windows: list[LoadedWindow] = []
    for window in windows:
        dataset = config.datasets[window.dataset]
        path = dataset.path
        if not path.is_file():
            raise DataError(
                f"Missing {dataset.id} dataset at {path}. Set {dataset.path_env}."
            )
        main_frame, readings, loaded = _load_one(
            path,
            window,
            appliances,
            sample_period,
            max_samples,
            dataset.mains_ac_type,
            dataset.appliance_ac_type,
        )
        mains.append(main_frame)
        for name in appliances:
            appliance_frames[name].append(readings[name])
        loaded_windows.append(loaded)

        requested_start = datetime.fromisoformat(window.start)
        requested_end = datetime.fromisoformat(window.end)
        available_start = datetime.fromisoformat(loaded.available_start)
        available_end = datetime.fromisoformat(loaded.available_end)
        tolerance = timedelta(seconds=sample_period)
        if requested_start + tolerance < available_start or requested_end > (
            available_end + tolerance
        ):
            message = (
                f"{task.id}: requested {window.start} to {window.end} from "
                f"{window.dataset} building {window.building}, but the common meter "
                f"envelope is {loaded.available_start} to {loaded.available_end}"
            )
            if task.coverage_policy == "strict":
                raise DataError(message)
            warnings.warn(message, stacklevel=2)

    return LoadedSplit(mains, appliance_frames, loaded_windows)
