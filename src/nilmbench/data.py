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
    effective_start: str
    effective_end: str
    available_start: str
    available_end: str
    actual_start: str
    actual_end: str
    samples: int
    expected_samples: int
    sample_limit: int | None
    aligned_sample_fraction: float
    resolved_appliances: dict[str, tuple[str, ...]]
    resolved_meters: dict[str, tuple[str, ...]]
    shared_meter_appliances: dict[str, tuple[str, ...]]
    resolved_mains_ac_type: str
    resolved_appliance_ac_types: dict[str, str]


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


def _load_series(
    meter: Any,
    *,
    ac_types: tuple[str, ...],
    sample_period: int,
) -> tuple[Any, str]:
    """Load every NILMTK chunk in the selected window into one frame."""
    import pandas as pd
    from nilmtk.exceptions import MeasurementError

    errors = []
    for ac_type in ac_types:
        try:
            chunks = list(
                meter.load(
                    physical_quantity="power",
                    ac_type=ac_type,
                    sample_period=sample_period,
                    verbose=False,
                )
            )
        except MeasurementError as exc:
            errors.append(f"{ac_type}: {exc}")
            continue
        if not chunks:
            errors.append(f"{ac_type}: NILMTK returned no chunks")
            continue
        frame = pd.concat(chunks, axis=0).sort_index()
        frame = frame[~frame.index.duplicated(keep="first")]
        return _first_column(frame), ac_type
    detail = "; ".join(errors)
    raise DataError(f"No configured AC type could be loaded ({detail})")


def _load_one(
    dataset_path: Path,
    window: WindowConfig,
    appliances: tuple[str, ...],
    sample_period: int,
    max_samples: int | None,
    mains_ac_types: tuple[str, ...],
    appliance_ac_types: tuple[str, ...],
    shared_meter_policy: str,
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
        resolved_appliances: dict[str, tuple[str, ...]] = {}
        resolved_meters: dict[str, tuple[str, ...]] = {}
        shared_meter_appliances: dict[str, tuple[str, ...]] = {}
        for appliance in appliances:
            try:
                meter = elec.select_using_appliances(type=appliance)
            except KeyError:
                meter = None
            if meter is None or not meter.meters:
                available = sorted(
                    {
                        item.identifier.type
                        for submeter in elec.submeters().meters
                        for item in submeter.appliances
                    }
                )
                raise DataError(
                    f"{window.dataset} building {window.building} lacks canonical "
                    f"NILM Metadata appliance {appliance!r}; "
                    f"available types: {', '.join(available)}"
                )
            selected_meters.append(meter)
            resolved_appliances[appliance] = tuple(
                sorted(
                    {
                        f"{item.identifier.type}#{item.identifier.instance}"
                        for selected in meter.meters
                        for item in selected.appliances
                    }
                )
            )
            resolved_meters[appliance] = tuple(
                sorted(str(selected.identifier) for selected in meter.meters)
            )
            shared = tuple(
                sorted(
                    {
                        f"{item.identifier.type}#{item.identifier.instance}"
                        for selected in meter.meters
                        for item in selected.appliances
                        if not item.matches({"type": appliance})
                    }
                )
            )
            shared_meter_appliances[appliance] = shared
            if shared:
                message = (
                    f"{window.dataset} building {window.building} canonical "
                    f"{appliance!r} uses a shared meter containing: {', '.join(shared)}"
                )
                if shared_meter_policy == "strict":
                    raise DataError(message)
                if shared_meter_policy == "warn":
                    warnings.warn(message, stacklevel=2)
        timeframes = [meter.get_timeframe() for meter in selected_meters]
        available_start = max(frame.start for frame in timeframes)
        available_end = min(frame.end for frame in timeframes)

        requested_start = datetime.fromisoformat(window.start)
        requested_end = datetime.fromisoformat(window.end)
        available_start_naive = available_start.tz_localize(None).to_pydatetime()
        available_end_naive = available_end.tz_localize(None).to_pydatetime()
        effective_start = max(requested_start, available_start_naive)
        if max_samples is not None and max_samples <= 0:
            raise DataError("max_samples must be positive")
        effective_end = min(requested_end, available_end_naive)
        if effective_start >= effective_end:
            raise DataError(
                f"No data inside the requested window for {window.dataset} "
                f"building {window.building}"
            )
        effective_start_text = effective_start.isoformat(sep=" ")
        effective_end_text = effective_end.isoformat(sep=" ")
        dataset.set_window(start=effective_start_text, end=effective_end_text)
        mains, resolved_mains_ac_type = _load_series(
            elec.mains(),
            ac_types=mains_ac_types,
            sample_period=sample_period,
        )
        readings: dict[str, Any] = {}
        resolved_appliance_ac_types: dict[str, str] = {}
        for appliance, meter in zip(appliances, selected_meters[1:], strict=True):
            readings[appliance], resolved_ac_type = _load_series(
                meter,
                ac_types=appliance_ac_types,
                sample_period=sample_period,
            )
            resolved_appliance_ac_types[appliance] = resolved_ac_type

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
        requested_seconds = (effective_end - effective_start).total_seconds()
        expected_samples = max(1, int(requested_seconds // sample_period))
        if max_samples is not None:
            expected_samples = min(expected_samples, max_samples)
        loaded = LoadedWindow(
            requested=window,
            effective_start=effective_start_text,
            effective_end=effective_end_text,
            available_start=available_start.tz_localize(None).isoformat(),
            available_end=available_end.tz_localize(None).isoformat(),
            actual_start=index[0].isoformat(),
            actual_end=index[-1].isoformat(),
            samples=len(index),
            expected_samples=expected_samples,
            sample_limit=max_samples,
            aligned_sample_fraction=len(index) / expected_samples,
            resolved_appliances=resolved_appliances,
            resolved_meters=resolved_meters,
            shared_meter_appliances=shared_meter_appliances,
            resolved_mains_ac_type=resolved_mains_ac_type,
            resolved_appliance_ac_types=resolved_appliance_ac_types,
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
            dataset.mains_ac_types,
            dataset.appliance_ac_types,
            task.shared_meter_policy,
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
