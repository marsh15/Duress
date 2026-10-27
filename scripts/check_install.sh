#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to build and check the wheel" >&2
  exit 2
fi
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "Python 3.12 is required for the clean installation check" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/duress-install.XXXXXXXX")"
trap 'rm -rf "$scratch"' EXIT
wheelhouse="$scratch/wheelhouse"
venv="$scratch/venv"
outside="$scratch/outside-checkout"
mkdir -p "$wheelhouse" "$outside"

cd "$repo_root"
uv build --wheel --out-dir "$wheelhouse"
uv venv --python python3.12 "$venv"
uv pip install --python "$venv/bin/python" "$wheelhouse"/duress-*.whl

export PATH="$venv/bin:$PATH"
export DURESS_HOME="$scratch/runtime-home"
unset PYTHONPATH DURESS_ROOT DURESS_LLM_CACHE || true
cd "$outside"

duress targets | tee "$scratch/targets.txt"
if ! grep -q 'razorpay-fixed' "$scratch/targets.txt"; then
  echo "installed CLI did not discover the bundled fixed target" >&2
  exit 1
fi

python - <<'PY'
from importlib.resources import files
from pathlib import Path

from duress.investigator import build_source_bundle
from duress.profiles import ProfileRegistry, repo_root

root = repo_root(Path.cwd())
assert root.name == "resources", root
profile = ProfileRegistry.discover().get("razorpay-fixed")
assert profile.seed_path.is_file()
assert (profile.target_dir / "fixed" / "handler.py").is_file()
bundle = build_source_bundle(profile)
assert "razorpay_ref/fixed/handler.py" in bundle.files
assert "razorpay_ref/common/signing.py" in bundle.files
assert list((root / "fixtures" / "llm").glob("*.json"))
assert (root / "showcase" / "investigation.json").is_file()
assert list((root / "showcase" / "dna").glob("*.json"))
package = files("duress")
assert package.joinpath("web", "templates", "base.html").is_file()
assert package.joinpath("web", "static", "site.css").is_file()
print(f"bundled resources verified from {root}")
PY

duress run razorpay-fixed --scenario S1 --policy explore
