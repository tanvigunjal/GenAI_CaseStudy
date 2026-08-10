#!/usr/bin/env python3
"""Fail closed when an evidence manifest or any bound input has changed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from passenger_forecast.evidence import EvidenceError, verify_evidence_manifest


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--raw-data-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        manifest = verify_evidence_manifest(
            args.manifest,
            repository_root=args.repository_root,
            raw_data_dir=args.raw_data_dir,
        )
    except EvidenceError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "status": "verified",
                "source_revision": manifest["source"]["revision"],
                "artifact_count": len(manifest["artifacts"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
