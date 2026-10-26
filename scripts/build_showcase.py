#!/usr/bin/env python3
"""Package already recorded M2 evidence. Does not run or simulate an experiment.

Usage: uv run python scripts/build_showcase.py --dna PATH
The supplied DNA must match the artifact cited by docs/measurements/m2.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from duress.models import DnaDocument, canonical_sha256
from duress.store import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]


def build(path: Path, snapshot: Path | None = None) -> None:
    document = json.loads(path.read_text())
    DnaDocument.model_validate(document)
    if canonical_sha256(document["reproducer"]) != document["reproducer_sha256"]:
        raise ValueError("DNA digest mismatch")
    measurement = json.loads((ROOT / "docs/measurements/m2.json").read_text())
    if document["dna_id"] != measurement["artifact"]["dna_id"]:
        raise ValueError("DNA is not the measured M2 artifact")
    scenario = document["provenance_scenario"]
    record = {
        "investigation_id": "showcase-m2", "showcase": True,
        "record_type": "recorded_m2_evidence", "target_id": document["provenance"]["target_id"],
        "status": "completed", "phase": "Recorded M2 benchmark",
        "mode_requested": "recorded", "mode_effective": "recorded",
        "created_at": document["provenance"]["created_at"],
        "hypotheses": [], "scenarios": [], "trials": [],
        "dna_ids": [document["dna_id"]], "errors": [],
        "recorded_summary": document["characterization"],
        "observed_attempts": document["observations"]["attempts"],
        "comparison": {
            "source": "docs/measurements/m2.json: portability.tail",
            "outcome": "VERIFIED", "control_target": "razorpay-vulnerable",
            "control_valid": 5, "control_same_signature_failures": 5,
            "candidate_target": "razorpay-fixed", "candidate_valid": 20,
            "note": "Historical M2 portability verification. Counts are transcribed from the retained command output; this page does not execute a verification.",
        },
        "source_evidence": ["docs/measurements/m2.json", "docs/EVIDENCE.md", f"showcase/dna/{document['dna_id']}.json"],
        "events": [
            {"phase": "Characterization", "message": f"Recorded {document['characterization']['fail']} failures in {document['characterization']['valid']} valid trials of {scenario['scenario_id']}.", "scenario_id": scenario["scenario_id"]},
            {"phase": "Representative trial", "message": "The retained DNA records two processed responses and an INV-1 duplicate_fulfilment violation after clean settlement.", "trial_id": document["observations"]["trial_id"]},
            {"phase": "Portable replay", "message": "M2 portability command recorded REPRODUCED, followed by VERIFIED against razorpay-fixed. See the retained measurement output."},
        ],
        "explanation": "Recorded synthetic benchmark from M2, not a live or autonomous investigation. The complete per-trial investigation stream was not retained: only the characterization aggregate, representative DNA observations and command output are shown. Source-grounded AI investigation and reduction remain pending.",
    }
    # Guard the transcription against the retained output rather than inventing counts.
    tail = measurement["portability"]["tail"]
    for expected in ["outcome    VERIFIED", "k=5 v=5 p_hat=1.0", "valid=20 n_c=20"]:
        if expected not in tail:
            raise ValueError("M2 measurement no longer supports showcase comparison")
    if snapshot is not None:
        # This is a read of retained evidence, never a target or experiment launch.
        expected = ROOT / document['observations']['evidence_ref'] / 'snapshot/app.db'
        if snapshot.resolve() != expected.resolve():
            raise ValueError('snapshot must belong to the recorded representative trial')
        with sqlite3.connect(snapshot.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables = {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]
                      for table in ('orders', 'payments', 'fulfilments')}
        record['business_state'] = {
            'baseline': document['reproducer']['seed']['tables'], 'observed': tables,
            'source': document['observations']['evidence_ref'] + '/snapshot/app.db',
            'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            'trial_id': document['observations']['trial_id'],
            'note': 'Retained M2 representative SQLite snapshot, read without starting a new experiment.',
        }
    atomic_write_json(ROOT / "showcase/dna" / path.name, document)
    atomic_write_json(ROOT / "showcase/investigation.json", record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dna", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, help="Optional retained representative M2 snapshot")
    args = parser.parse_args()
    build(args.dna, args.snapshot)
