"""Source protocols and local CSV vertical-slice implementations."""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

from passenger_forecast.contracts import ForecastRecord, SourceSnapshot


@runtime_checkable
class StationMetadataSource(Protocol):
    def read(self) -> pd.DataFrame: ...

    def snapshot(self) -> SourceSnapshot: ...


@runtime_checkable
class PassengerHistorySource(Protocol):
    def read(self) -> pd.DataFrame: ...

    def snapshot(self) -> SourceSnapshot: ...


@runtime_checkable
class TrafficHistorySource(Protocol):
    def read(self) -> pd.DataFrame: ...

    def snapshot(self) -> SourceSnapshot: ...


@runtime_checkable
class WeatherForecastSource(Protocol):
    def read(self) -> pd.DataFrame: ...

    def snapshot(self) -> SourceSnapshot: ...


@runtime_checkable
class ForecastSink(Protocol):
    def publish(self, records: tuple[ForecastRecord, ...]) -> Path: ...


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CSVSource:
    """Immutable CSV snapshot adapter; schema validation is handled by ``data``."""

    def __init__(
        self,
        path: Path,
        *,
        source_name: str,
        schema_version: str = "1.0.0",
        timestamp_column: str = "timestamp",
    ) -> None:
        self.path = path
        self.source_name = source_name
        self.schema_version = schema_version
        self.timestamp_column = timestamp_column

    def read(self) -> pd.DataFrame:
        if not self.path.is_file():
            raise FileNotFoundError(f"missing source file: {self.path}")
        return pd.read_csv(self.path)

    def snapshot(self) -> SourceSnapshot:
        frame = self.read()
        now = datetime.now(UTC)
        watermark = now
        if self.timestamp_column in frame and len(frame):
            parsed = pd.to_datetime(frame[self.timestamp_column], utc=True, errors="raise")
            watermark = parsed.max().to_pydatetime()
        return SourceSnapshot(
            source_name=self.source_name,
            extracted_at=now,
            watermark=watermark,
            row_count=len(frame),
            schema_version=self.schema_version,
            digest=sha256_file(self.path),
        )


class CSVStationMetadataSource(CSVSource):
    def __init__(self, path: Path, schema_version: str = "1.0.0") -> None:
        super().__init__(path, source_name="station_metadata", schema_version=schema_version)


class CSVPassengerHistorySource(CSVSource):
    def __init__(self, path: Path, schema_version: str = "1.0.0") -> None:
        super().__init__(path, source_name="timeseries_with_target", schema_version=schema_version)


class CSVTrafficHistorySource(CSVSource):
    def __init__(self, path: Path, schema_version: str = "1.0.0") -> None:
        super().__init__(path, source_name="traffic_hourly", schema_version=schema_version)


class CSVWeatherForecastSource(CSVSource):
    def __init__(self, path: Path, schema_version: str = "1.0.0") -> None:
        super().__init__(path, source_name="weather_hourly", schema_version=schema_version)


class AtomicCSVForecastSink:
    """Write a complete forecast atomically after validating unique target keys."""

    def __init__(self, output_path: Path, expected_records: int | None = None) -> None:
        self.output_path = output_path
        self.expected_records = expected_records

    def publish(self, records: tuple[ForecastRecord, ...]) -> Path:
        if self.expected_records is not None and len(records) != self.expected_records:
            raise ValueError(f"expected {self.expected_records} records, received {len(records)}")
        keys = {(record.station_id, record.target_timestamp) for record in records}
        if len(keys) != len(records):
            raise ValueError("forecast contains duplicate station/timestamp keys")
        if not records:
            raise ValueError("refusing to publish an empty forecast")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [record.model_dump(mode="json") for record in records]
        fd, temporary = tempfile.mkstemp(
            dir=self.output_path.parent,
            prefix=f".{self.output_path.name}.",
            suffix=".tmp",
        )
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            pd.DataFrame(rows).to_csv(temporary_path, index=False)
            os.replace(temporary_path, self.output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return self.output_path
