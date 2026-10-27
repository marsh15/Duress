from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from duress import profiles
from duress.investigator import build_source_bundle
from duress.profiles import ProfileRegistry, repo_root

ROOT = Path(__file__).resolve().parents[2]


def test_checkout_discovery_keeps_contributor_resources():
    assert repo_root(ROOT / "tests") == ROOT
    registry = ProfileRegistry.discover(ROOT)
    profile = registry.get("razorpay-fixed")
    assert profile.cwd == ROOT
    assert profile.seed_path.is_file()
    assert profile.target_dir.joinpath("fixed", "handler.py").is_file()


def test_source_bundle_has_target_code_needed_for_fixed_scenario():
    profile = ProfileRegistry.discover(ROOT).get("razorpay-fixed")
    bundle = build_source_bundle(profile)

    assert "razorpay_ref/fixed/handler.py" in bundle.files
    assert "razorpay_ref/common/signing.py" in bundle.files
    assert "razorpay_ref/profile.toml" not in bundle.files


def test_python_package_contains_web_templates_and_static_assets():
    package = files("duress")

    assert package.joinpath("web", "templates", "base.html").is_file()
    assert package.joinpath("web", "static", "site.css").is_file()
    assert package.joinpath("web", "static", "site.js").is_file()


def test_repo_root_uses_bundled_resources_when_no_checkout_is_available(tmp_path, monkeypatch):
    package_dir = tmp_path / "site-packages" / "duress"
    resource_root = package_dir / "resources"
    target_dir = resource_root / "targets" / "sample"
    target_dir.mkdir(parents=True)
    (target_dir / "profile.toml").write_text(
        """
[profile]
id = "sample"
domain = "sample"
contract_version = 1

[seed]
path = "seed.json"

[signing]
scheme = "none"
header = "x-signature"
event_id_header = "x-event-id"
secret_ref = "TEST_SECRET"

[variants.control]
app = "sample.app:create"
init = ["python", "-m", "sample"]
serve = ["python", "-m", "sample"]
"""
    )
    monkeypatch.setattr(profiles, "__file__", str(package_dir / "profiles.py"))
    monkeypatch.delenv("DURESS_ROOT", raising=False)

    assert repo_root(tmp_path / "outside-project") == resource_root
    registry = ProfileRegistry.discover(tmp_path / "outside-project")
    assert registry.root == resource_root
    assert registry.get("sample-control").profile_id == "sample"


def test_repository_has_model_fixtures_and_recorded_showcase_resources():
    assert list((ROOT / "fixtures" / "llm").glob("*.json"))
    assert (ROOT / "showcase" / "investigation.json").is_file()
    assert list((ROOT / "showcase" / "dna").glob("*.json"))


def test_cli_exports_bundled_showcase_outside_checkout(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from duress.cli import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DURESS_ROOT", str(ROOT))
    monkeypatch.delenv("DURESS_HOME", raising=False)
    output = tmp_path / "report.html"
    result = CliRunner().invoke(app, ["report", "showcase", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert "showcase-m2" in output.read_text()
    assert "SIL OPEN FONT LICENSE" in output.read_text()
