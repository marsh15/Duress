# Productization CI

The productization workflows check the installable package and exercise the
external integration against an independent ticketing application. They use
Python 3.12, a per-run PostgreSQL container, and a database named with the
`_test` suffix.

## Reusable workflow for a caller project

`.github/workflows/external-ci.yml` is a `workflow_call` workflow. A caller
checks out its own trusted application repository, installs Duress from the
configured public source ref, optionally installs a caller-owned
`requirements.txt`, and runs `duress check` against the caller's config. By
default the app is in the caller checkout. Set `application-bundle` to a
relative path for a trusted Git bundle in that checkout to have the workflow
clone it into `RUNNER_TEMP`, outside the Duress source checkout. In bundle mode,
`application-path` and `config-path` are relative to the cloned bundle; without
a bundle, `application-path` is relative to the caller checkout. The workflow
validates `application-path`, `app-requirements-path`, and `config-path` to
remain under their selected application root. Only pass a bundle and
requirements file from repositories you trust; pip installing either can run
application build and dependency code. `install-application: true`
installs the app package into the same virtual environment as Duress, and is
off by default.

Pin the workflow and the Duress source to the same immutable commit:

```yaml
jobs:
  duress:
    permissions:
      contents: read
    uses: marsh15/Duress/.github/workflows/external-ci.yml@<duress-commit-sha>
    with:
      duress-ref: <duress-commit-sha>
      application-bundle: examples/ticketing.bundle
      application-path: .
      app-requirements-path: requirements-ci.txt
      config-path: duress/duress.toml
      scenario: concurrent-duplicate
      install-application: true
```

`duress-ref` is required because the called workflow runs in the caller's GitHub
context and cannot infer which Duress ref supplied it. Omit
`app-requirements-path` when the target has no additional Python dependencies.
The config's PostgreSQL observer should use
`database_ref = "DURESS_TICKETING_DB"`; the workflow supplies that variable at
runtime. It runs the selected scenario when supplied, otherwise all configured
scenarios.

The workflow creates a fresh random PostgreSQL password at runtime, masks it,
binds a randomly assigned host port on `127.0.0.1`, and removes the container
afterward. No external database credential or configured password is needed.
The config contains only the environment variable reference. The runner keeps
JSON, JUnit XML, and exported Failure DNA in a temporary artifact directory and
uploads them even when a step fails. Artifacts are retained for 14 days; the
database DSN and runtime config are not uploaded.

The gate treats `PASS` as exit 0, `FAIL` as 1, `INCONCLUSIVE` as 2, configuration
or operational errors as 3, and a rejected artifact or operation as 4. Every
nonzero status fails the workflow. The `duress check` command emits 0, 1, 2, or
3; replay, verification, and reduction operations can additionally return 4
for `REJECTED`.

## Internal regression and independent ticketing controls

`.github/workflows/productization.yml` runs on pull requests and pushes to
`main`. Its `reusable-fixed` job calls `.github/workflows/external-ci.yml` with
the current commit, `examples/ticketing.bundle`, and the independent app's
`duress/fixed.toml`. It installs that app package in the reusable workflow's
Duress environment, starts the workflow's isolated PostgreSQL runtime, and
runs the actual `duress check --scenario concurrent-duplicate` gate. This
selftest exercises the reusable workflow itself and uploads its JSON, JUnit,
and Failure DNA artifacts even when the check fails.

The separate `productization` job syncs the lockfile with `uv sync --locked`,
runs the complete pytest suite, builds the wheel and source distribution,
installs the wheel with its PostgreSQL extra in a fresh virtual environment,
then checks the installed `duress` command from outside the source checkout.

The standalone acceptance steps also clone `examples/ticketing.bundle` into
`RUNNER_TEMP`. That Git bundle is produced from the standalone sibling
ticketing repository, which does not import Duress. Those steps install the
application into the clean Duress environment, run
`scripts/setup_ticketing_example.py APP`, and invoke
`scripts/ci_ticketing.py APP`. This script is a broader acceptance control; it
does not call the reusable workflow and does not replace the `reusable-fixed`
selftest. It runs the selected
`concurrent-duplicate` scenario and requires:

- The flawed application returns `FAIL` (exit 1) and produces real
  `external-dna@1` evidence.
- The fixed application returns `PASS` (exit 0).
- Replay of the exported flawed Failure DNA returns `REPRODUCED` (exit 1).
- Verification of the fixed candidate returns `VERIFIED` (exit 0).
- Verification of the flawed candidate returns `NOT_VERIFIED` (exit 1).

Each subprocess's expected and actual exit code and parsed outcome are saved in
`summary.json`. JSON and JUnit reports, DNA, and replay/verification records go
under the runner's temporary productization artifact directory. The script
checks that neither the DSN nor its password appears in saved operation
records. `--reduce` optionally runs bounded reduction; it records Duress's
returned result and makes no independent minimality claim.

The ticketing database password is generated per workflow run, masked before
use, and never written to the cloned app config or uploaded artifacts. The
DSN is passed as `DURESS_TICKETING_DB`; all generated manifests refer to it by
environment-variable name. PostgreSQL listens only on loopback, and the
database name ends in `_test` as required by the independent app's guard.

Run the same acceptance script locally after installing the built Duress wheel
and the independent application into one Python 3.12 environment, generating
the app configs, and setting `DURESS_TICKETING_DB` to a local loopback
`*_test` database URI:

```sh
python scripts/setup_ticketing_example.py /path/to/Duress-ticketing
.venv/bin/python scripts/ci_ticketing.py /path/to/Duress-ticketing \
  --artifacts-dir /tmp/duress-ticketing-artifacts
```

The acceptance script invokes `sys.executable` for every Duress CLI operation,
so the interpreter used for the script must have the Duress wheel and
PostgreSQL extra installed. It does not activate or infer an environment from
the current shell's `duress` command.

## Merge protection

The workflows publish check results; they do not change repository settings.
Repository administrators must enable branch protection or a ruleset that
requires the `Productization regression and ticketing controls / productization`
status check before merges are blocked by this gate.
