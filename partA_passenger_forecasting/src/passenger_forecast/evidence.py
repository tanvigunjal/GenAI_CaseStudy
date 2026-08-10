"""Portable evidence manifests with fail-closed integrity verification."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

EVIDENCE_SCHEMA_VERSION = "1.0.0"
SOURCE_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_DATASET_SHA256 = {
    "station_metadata.csv": "f619def0c760d9b9d01b1614252a15ea22dc3db987cfac1227c34db78ab9ff93",
    "timeseries_with_target.csv": (
        "60693930f3de3415124c9334188eae51e34a0545f39098c3508463b5421f13b0"
    ),
    "traffic_hourly.csv": "723ea8ccb6e2e886ee028771b3a1853f8f0f77a8b6afe76ac598d2bdf2fb830a",
    "weather_hourly.csv": "1e34227dbc2b65f375e20d0ca19ac50a4ca65d24f9267dded015266edfa64698",
}
REQUIRED_ARTIFACTS = frozenset(
    {"model", "feature_schema", "predictions", "run_log", "evaluation_manifest"}
)


class EvidenceError(RuntimeError):
    """Evidence is absent, inconsistent, stale, or has failed an integrity check."""


def sha256_file(path: Path) -> str:
    """Hash a file without loading it into memory."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise EvidenceError(f"cannot read evidence input: {path}") from exc
    return digest.hexdigest()


def _stable_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid JSON evidence: {path}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"evidence root must be an object: {path}")
    return value


def _within_repository(path: Path, repository_root: Path) -> tuple[Path, str]:
    resolved_root = repository_root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise EvidenceError(f"evidence path must be within repository: {resolved}") from exc
    if not resolved.is_file():
        raise EvidenceError(f"evidence path is not a file: {resolved}")
    return resolved, relative.as_posix()


def _file_record(path: Path, repository_root: Path) -> dict[str, Any]:
    resolved, relative = _within_repository(path, repository_root)
    return {"path": relative, "sha256": sha256_file(resolved), "bytes": resolved.stat().st_size}


def _records(
    paths: Sequence[Path], repository_root: Path, *, disallow_empty: bool = True
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in paths:
        record = _file_record(path, repository_root)
        relative = str(record["path"])
        if relative in records:
            raise EvidenceError(f"duplicate integrity path: {relative}")
        records[relative] = record
    if disallow_empty and not records:
        raise EvidenceError("integrity path list must not be empty")
    return records


def _coverage(evaluation: Mapping[str, Any]) -> tuple[int, int]:
    candidates: list[Mapping[str, Any]] = [evaluation]
    for key in ("coverage", "official_metrics", "official_holdout"):
        nested = evaluation.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    prediction_keys = (
        "prediction_count",
        "predictions",
        "total_predictions",
        "record_count",
        "forecast_rows",
    )
    score_keys = (
        "scoreable_count",
        "scored_labels",
        "observed_labels",
        "n_scored",
        "scoreable_rows",
    )
    prediction_count: int | None = None
    scoreable_count: int | None = None
    for candidate in candidates:
        for key in prediction_keys:
            value = candidate.get(key)
            if isinstance(value, int):
                prediction_count = value
                break
        for key in score_keys:
            value = candidate.get(key)
            if isinstance(value, int):
                scoreable_count = value
                break
    if prediction_count is None or scoreable_count is None:
        raise EvidenceError("evaluation must record prediction_count and scoreable_count")
    return prediction_count, scoreable_count


def _overall_rmse(evaluation: Mapping[str, Any]) -> float:
    candidates: list[Mapping[str, Any]] = [evaluation]
    for key in ("official_metrics", "official_holdout", "metrics"):
        nested = evaluation.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    for candidate in candidates:
        for key in ("overall_rmse", "official_overall_rmse"):
            value = candidate.get(key)
            if isinstance(value, int | float) and math.isfinite(float(value)) and float(value) >= 0:
                return float(value)
    raise EvidenceError("evaluation must contain a finite non-negative overall_rmse")


def _station_results(evaluation: Mapping[str, Any]) -> list[dict[str, Any]]:
    value: Any = None
    for key in ("per_station_results", "per_station", "station_metrics"):
        if key in evaluation:
            value = evaluation[key]
            break
        official = evaluation.get("official_metrics")
        if isinstance(official, Mapping) and key in official:
            value = official[key]
            break
    if isinstance(value, Mapping):
        rows = []
        for station_id, item in value.items():
            row = dict(item) if isinstance(item, Mapping) else {"rmse": item}
            row.setdefault("station_id", station_id)
            rows.append(row)
    elif isinstance(value, list):
        rows = [dict(item) for item in value if isinstance(item, Mapping)]
    else:
        raise EvidenceError("evaluation must contain per-station results")
    if len(rows) != 10:
        raise EvidenceError(f"evaluation has {len(rows)} station results; expected 10")
    seen: set[str] = set()
    sample_counts: list[int] = []
    for row in rows:
        station_id = str(row.get("station_id", row.get("station", "")))
        rmse = row.get("rmse")
        sample_count = row.get("sample_count", row.get("n"))
        if not station_id or station_id in seen:
            raise EvidenceError("per-station results require ten unique station IDs")
        if not isinstance(rmse, int | float) or not math.isfinite(float(rmse)) or float(rmse) < 0:
            raise EvidenceError(f"station {station_id} has invalid RMSE")
        if not isinstance(sample_count, int) or sample_count <= 0:
            raise EvidenceError(f"station {station_id} has invalid sample_count")
        seen.add(station_id)
        sample_counts.append(sample_count)
    if sorted(sample_counts) != [41, 41, 41, 42, 42, 42, 42, 42, 42, 42]:
        raise EvidenceError("per-station sample counts do not reconcile to the 417 observed labels")
    return rows


def validate_evaluation_contract(evaluation: Mapping[str, Any]) -> None:
    """Require the locked official holdout contract before evidence can be exported."""

    prediction_count, scoreable_count = _coverage(evaluation)
    if prediction_count != 420 or scoreable_count != 417:
        raise EvidenceError(
            f"official coverage is {prediction_count}/{scoreable_count}; expected 420/417"
        )
    _overall_rmse(evaluation)
    _station_results(evaluation)


def _validate_split(split: Mapping[str, Any]) -> None:
    required = {
        "train_start": "2023-01-01",
        "train_end": "2026-01-26",
        "test_start": "2026-01-27",
        "test_end": "2026-02-02",
        "train_rows": 67_320,
        "test_rows": 420,
    }
    mismatches = {
        key: (split.get(key), expected)
        for key, expected in required.items()
        if split.get(key) != expected
    }
    if mismatches:
        raise EvidenceError(f"split does not match the locked global-date contract: {mismatches}")


def _validate_dataset_fingerprints(fingerprints: Mapping[str, str]) -> dict[str, dict[str, str]]:
    by_name = {Path(name).name: digest for name, digest in fingerprints.items()}
    missing = sorted(set(EXPECTED_DATASET_SHA256) - set(by_name))
    extra = sorted(set(by_name) - set(EXPECTED_DATASET_SHA256))
    if missing or extra:
        raise EvidenceError(
            "dataset fingerprints differ from the four-file contract: "
            f"missing={missing}, extra={extra}"
        )
    result: dict[str, dict[str, str]] = {}
    for name, expected in EXPECTED_DATASET_SHA256.items():
        actual = by_name[name]
        if actual != expected:
            raise EvidenceError(f"dataset fingerprint mismatch for {name}")
        result[name] = {"sha256": actual, "expected_sha256": expected}
    return result


def _validate_run_metadata(
    host_profile: Mapping[str, Any],
    resource_limits: Mapping[str, Any],
    resource_usage: Mapping[str, Any],
) -> None:
    if not host_profile:
        raise EvidenceError("host_profile is required")
    required_limits = {"cpu_threads", "max_rss_gib", "max_tuning_seconds"}
    if not required_limits.issubset(resource_limits):
        raise EvidenceError(f"resource_limits must include {sorted(required_limits)}")
    if int(resource_limits["cpu_threads"]) > 6:
        raise EvidenceError("remote tuning used more than six CPU threads")
    if float(resource_limits["max_rss_gib"]) > 8.0:
        raise EvidenceError("remote tuning limit exceeded 8 GiB")
    if int(resource_limits["max_tuning_seconds"]) > 7_200:
        raise EvidenceError("remote tuning limit exceeded two hours")
    elapsed = resource_usage.get("elapsed_seconds")
    peak_rss = resource_usage.get("peak_rss_gib")
    if not isinstance(elapsed, int | float) or float(elapsed) < 0:
        raise EvidenceError("resource_usage.elapsed_seconds is required")
    if not isinstance(peak_rss, int | float) or float(peak_rss) < 0:
        raise EvidenceError("resource_usage.peak_rss_gib is required")


def _validate_tabpfn(record: Mapping[str, Any]) -> None:
    disposition = record.get("disposition", record.get("status"))
    if disposition != "skipped_resource_and_fit_gate":
        raise EvidenceError("TabPFN v2 disposition must remain skipped_resource_and_fit_gate")


def _validate_prediction_file(path: Path) -> None:
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path)
        elif path.suffix.lower() in {".parquet", ".pq"}:
            frame = pd.read_parquet(path)
        else:
            raise EvidenceError("prediction artifact must be CSV or Parquet")
    except (OSError, ValueError) as exc:
        raise EvidenceError("prediction artifact cannot be read") from exc
    if len(frame) != 420:
        raise EvidenceError(f"prediction artifact has {len(frame)} rows; expected 420")
    station = "station_id" if "station_id" in frame else "station"
    timestamp = "target_timestamp" if "target_timestamp" in frame else "timestamp"
    if "forecast_continuous" in frame:
        forecast = "forecast_continuous"
    elif "forecast" in frame:
        forecast = "forecast"
    else:
        forecast = "prediction"
    if not {station, timestamp, forecast}.issubset(frame.columns):
        raise EvidenceError(
            "prediction artifact does not satisfy the station/time/forecast contract"
        )
    if frame[[station, timestamp]].duplicated().any() or frame[station].nunique() != 10:
        raise EvidenceError(
            "prediction artifact contains duplicate keys or does not cover ten stations"
        )
    values = pd.to_numeric(frame[forecast], errors="coerce")
    if values.isna().any() or not values.map(math.isfinite).all() or (values < 0).any():
        raise EvidenceError("prediction artifact contains invalid forecasts")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def export_evidence_manifest(
    *,
    output_path: Path,
    repository_root: Path,
    source_revision: str,
    source_files: Sequence[Path],
    dependency_lock: Path,
    configuration_files: Sequence[Path],
    artifact_files: Mapping[str, Path],
    dataset_fingerprints: Mapping[str, str],
    split: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    host_profile: Mapping[str, Any],
    resource_limits: Mapping[str, Any],
    resource_usage: Mapping[str, Any],
    tabpfn: Mapping[str, Any],
    model_candidates: Sequence[Mapping[str, Any]],
    ablations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build and atomically write a portable, self-checking evidence manifest."""

    if not SOURCE_REVISION_PATTERN.fullmatch(source_revision):
        raise EvidenceError("source_revision must be the full lowercase 40-character commit SHA")
    validate_evaluation_contract(evaluation)
    _validate_split(split)
    datasets = _validate_dataset_fingerprints(dataset_fingerprints)
    _validate_run_metadata(host_profile, resource_limits, resource_usage)
    _validate_tabpfn(tabpfn)
    source_records = _records(source_files, repository_root)
    configuration_records = _records(configuration_files, repository_root)
    lock_record = _file_record(dependency_lock, repository_root)
    missing_artifacts = sorted(REQUIRED_ARTIFACTS - set(artifact_files))
    if missing_artifacts:
        raise EvidenceError(f"required artifacts are missing: {missing_artifacts}")
    artifact_records = {
        name: _file_record(path, repository_root) for name, path in sorted(artifact_files.items())
    }
    prediction_path = repository_root / str(artifact_records["predictions"]["path"])
    _validate_prediction_file(prediction_path)
    evaluation_path = repository_root / str(artifact_records["evaluation_manifest"]["path"])
    if _load_json(evaluation_path) != dict(evaluation):
        raise EvidenceError("embedded evaluation differs from the evaluation_manifest artifact")
    manifest: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "revision": source_revision,
            "source_files": source_records,
            "source_tree_sha256": _stable_digest(source_records),
        },
        "dependency_lock": lock_record,
        "configuration": {
            "files": configuration_records,
            "configuration_sha256": _stable_digest(configuration_records),
        },
        "datasets": datasets,
        "split": dict(split),
        "model_candidates": [dict(item) for item in model_candidates],
        "evaluation": dict(evaluation),
        "ablations": [dict(item) for item in ablations],
        "tabpfn": dict(tabpfn),
        "run": {
            "host_profile": dict(host_profile),
            "resource_limits": dict(resource_limits),
            "resource_usage": dict(resource_usage),
        },
        "artifacts": artifact_records,
    }
    content = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    _atomic_write(output_path, content)
    digest = sha256_file(output_path)
    sidecar = output_path.with_name(f"{output_path.name}.sha256")
    _atomic_write(sidecar, f"{digest}  {output_path.name}\n")
    return manifest


def _verify_record(record: Mapping[str, Any], repository_root: Path, *, label: str) -> Path:
    relative = record.get("path")
    digest = record.get("sha256")
    size = record.get("bytes")
    if (
        not isinstance(relative, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise EvidenceError(f"{label} has an unsafe path")
    path = (repository_root / relative).resolve()
    try:
        path.relative_to(repository_root.resolve())
    except ValueError as exc:
        raise EvidenceError(f"{label} escapes repository root") from exc
    if not path.is_file() or not isinstance(digest, str) or sha256_file(path) != digest:
        raise EvidenceError(f"{label} hash verification failed")
    if not isinstance(size, int) or path.stat().st_size != size:
        raise EvidenceError(f"{label} size verification failed")
    return path


def _verify_git_revision(
    *, repository_root: Path, revision: str, source_records: Mapping[str, Mapping[str, Any]]
) -> None:
    try:
        check = subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "-e", f"{revision}^{{commit}}"],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise EvidenceError("git is required to verify the recorded source revision") from exc
    if check.returncode != 0:
        raise EvidenceError("recorded source revision does not exist in this repository")
    for relative, record in source_records.items():
        blob = subprocess.run(
            ["git", "-C", str(repository_root), "show", f"{revision}:{relative}"],
            check=False,
            capture_output=True,
        )
        if blob.returncode != 0 or hashlib.sha256(blob.stdout).hexdigest() != record.get("sha256"):
            raise EvidenceError(f"source file is not bound to recorded revision: {relative}")


def verify_evidence_manifest(
    manifest_path: Path,
    *,
    repository_root: Path,
    raw_data_dir: Path | None = None,
    verify_source_revision: bool = True,
) -> dict[str, Any]:
    """Verify the sidecar, all watched inputs/artifacts, and semantic run invariants."""

    sidecar = manifest_path.with_name(f"{manifest_path.name}.sha256")
    try:
        parts = sidecar.read_text(encoding="utf-8").strip().split()
    except OSError as exc:
        raise EvidenceError("evidence checksum sidecar is missing") from exc
    if len(parts) != 2 or parts[1] != manifest_path.name or parts[0] != sha256_file(manifest_path):
        raise EvidenceError("evidence manifest checksum verification failed")
    manifest = _load_json(manifest_path)
    if manifest.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceError("unsupported evidence schema version")
    source = manifest.get("source")
    configuration = manifest.get("configuration")
    if not isinstance(source, Mapping) or not isinstance(configuration, Mapping):
        raise EvidenceError("manifest source/configuration sections are malformed")
    revision = source.get("revision")
    source_records = source.get("source_files")
    config_records = configuration.get("files")
    if not isinstance(revision, str) or not SOURCE_REVISION_PATTERN.fullmatch(revision):
        raise EvidenceError("manifest source revision is malformed")
    if not isinstance(source_records, Mapping) or not source_records:
        raise EvidenceError("manifest does not bind source files")
    if not isinstance(config_records, Mapping) or not config_records:
        raise EvidenceError("manifest does not bind configuration files")
    for relative, record in source_records.items():
        if not isinstance(relative, str) or not isinstance(record, Mapping):
            raise EvidenceError("source file record is malformed")
        _verify_record(record, repository_root, label=f"source:{relative}")
    for relative, record in config_records.items():
        if not isinstance(relative, str) or not isinstance(record, Mapping):
            raise EvidenceError("configuration file record is malformed")
        _verify_record(record, repository_root, label=f"configuration:{relative}")
    if source.get("source_tree_sha256") != _stable_digest(source_records):
        raise EvidenceError("source tree aggregate hash mismatch")
    if configuration.get("configuration_sha256") != _stable_digest(config_records):
        raise EvidenceError("configuration aggregate hash mismatch")
    lock = manifest.get("dependency_lock")
    if not isinstance(lock, Mapping):
        raise EvidenceError("dependency lock record is missing")
    _verify_record(lock, repository_root, label="dependency_lock")
    if verify_source_revision:
        _verify_git_revision(
            repository_root=repository_root, revision=revision, source_records=source_records
        )

    datasets = manifest.get("datasets")
    if not isinstance(datasets, Mapping):
        raise EvidenceError("dataset section is malformed")
    fingerprints = {
        str(name): str(record.get("sha256"))
        for name, record in datasets.items()
        if isinstance(record, Mapping)
    }
    _validate_dataset_fingerprints(fingerprints)
    if raw_data_dir is not None:
        for name, expected in EXPECTED_DATASET_SHA256.items():
            path = raw_data_dir / name
            if not path.is_file() or sha256_file(path) != expected:
                raise EvidenceError(f"local raw-data verification failed for {name}")

    split = manifest.get("split")
    evaluation = manifest.get("evaluation")
    tabpfn = manifest.get("tabpfn")
    run = manifest.get("run")
    artifacts = manifest.get("artifacts")
    if not isinstance(split, Mapping):
        raise EvidenceError("split evidence section is malformed")
    if not isinstance(evaluation, Mapping):
        raise EvidenceError("evaluation evidence section is malformed")
    if not isinstance(tabpfn, Mapping):
        raise EvidenceError("TabPFN evidence section is malformed")
    if not isinstance(run, Mapping):
        raise EvidenceError("run evidence section is malformed")
    if not isinstance(artifacts, Mapping):
        raise EvidenceError("one or more required evidence sections are malformed")
    _validate_split(split)
    validate_evaluation_contract(evaluation)
    _validate_tabpfn(tabpfn)
    host_profile = run.get("host_profile")
    resource_limits = run.get("resource_limits")
    resource_usage = run.get("resource_usage")
    if not isinstance(host_profile, Mapping):
        raise EvidenceError("host profile is malformed")
    if not isinstance(resource_limits, Mapping):
        raise EvidenceError("resource limits are malformed")
    if not isinstance(resource_usage, Mapping):
        raise EvidenceError("resource usage is malformed")
    _validate_run_metadata(host_profile, resource_limits, resource_usage)
    missing = sorted(REQUIRED_ARTIFACTS - set(artifacts))
    if missing:
        raise EvidenceError(f"manifest omits required artifacts: {missing}")
    verified_artifacts: dict[str, Path] = {}
    for name, record in artifacts.items():
        if not isinstance(name, str) or not isinstance(record, Mapping):
            raise EvidenceError("artifact record is malformed")
        verified_artifacts[name] = _verify_record(record, repository_root, label=f"artifact:{name}")
    _validate_prediction_file(verified_artifacts["predictions"])
    if _load_json(verified_artifacts["evaluation_manifest"]) != dict(evaluation):
        raise EvidenceError("embedded evaluation no longer matches its artifact")
    return manifest
