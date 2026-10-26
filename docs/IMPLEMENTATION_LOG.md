# Implementation log

Milestone progress, engineering problems that were diagnosed rather than
patched around, and the verified outcome of each checkpoint. Claims here are
tied to a command that was actually run; where an environment prevented a
check, the entry says so.

## M0 — reproducible defect (commit 60fb965)

Delivered the reference integration (`vulnerable`, `fixed`), the independent
`tests/m0` harness, the M0 gate and generated evidence. Verified locally and in
Docker: E1 20/20 double fulfils, E2 20/20 exactly-once at 2 and 5 parties, E3
10/10 clean drains after SIGTERM, target suite green, and the four gate tests
collected exactly.

## M1 — trusted execution, oracle and isolation (commit cfdf2b4)

Delivered limits, models, `exec@1`, the phased supervisor, the isolated provider
stub, the six-member invariant pack, expectations, the oracle, aggregation, S1,
S2, S3, S8, the fixture controls, and `duress run` / `sweep`.

### M1 verification interruption and recovery

The first M1 evidence run was interrupted. It left three problems:

1. `docs/measurements/e1/e2/e3.json` had been overwritten by a macOS run of the
   M0 experiments launched by the interrupted script.
2. `docs/measurements/m1.json` recorded 2 failures from **before** the working
   tree fixes: a `PermissionError` in the supervisor and a retry test that
   produced two timeouts.
3. The uncommitted "fix" for the first failure treated `EPERM` from `killpg` as
   proof that a process group had terminated.

Recovery:

- Restored `e1/e2/e3.json` to the last complete Docker evidence rather than
  keeping the interrupted macOS overwrite. Those experiments were not re-run
  before the M1 code changed, so their numbers still describe the same target
  bytes.
- Replaced the `EPERM` shortcut with process-table liveness (D-M1-10). A probe
  showed `killpg` can report `EPERM` for a group containing a live member, so
  the shortcut could have allowed state to be read while a target was writing.
  `tests/unit/test_group_liveness.py` covers the refusal, the zombie-only case,
  per-pid escalation, own-group protection, and a sweep that must keep an
  unkillable group's pidfile.
- Fixed the retry test by removing the sub-100 ms latency assumption it was
  never testing (D-M1-11), and documented the C2/C3 margin changes.

**Verified outcome (this tree):**

| Check | Command | Result |
|---|---|---|
| Fast suite, local | `uv run pytest -q -m "not slow"` | 147 passed, 3 deselected, 187 s |
| Fast suite, container | `docker run ... duress:m1 uv run pytest -q -m "not slow"` | 147 passed, 182 s |
| M0 gate, local | `scripts/m0_gate.sh --gates-only` | pass, 4 gate tests, 75 s |
| M0 gate, container | `docker run ... scripts/m0_gate.sh --gates-only` | pass, 4 gate tests, 81 s |
| Evidence | `scripts/m1_evidence.py --label local|docker` | exit 0, `docs/EVIDENCE.md` regenerated |

Measurements: `docs/measurements/m1.json` and `m1-docker.json` were written by
those runs. The failed 12:33 measurement was not kept as current evidence.
