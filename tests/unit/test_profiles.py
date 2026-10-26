"""F1: target profiles declare the real launch command (never stock uvicorn)."""

from __future__ import annotations

from duress.profiles import ProfileRegistry


def test_every_variant_declares_init_and_serve(registry: ProfileRegistry):
    for profile in registry.all():
        assert profile.init_command
        assert profile.serve_command
        assert profile.app


def test_reference_targets_launch_through_the_drain_aware_launcher(registry):
    for target_id in ("razorpay-vulnerable", "razorpay-fixed", "razorpay-fixed-2w"):
        profile = registry.get(target_id)
        command = " ".join(profile.serve_command)
        assert "razorpay_ref.serve" in command
        assert "uvicorn" not in command
        assert "{host}" in profile.serve_command
        assert "{port}" in profile.serve_command
        assert "{workers}" in profile.serve_command


def test_init_commands_receive_db_and_seed(registry):
    for profile in registry.all():
        joined = " ".join(profile.init_command)
        assert "{db}" in joined
        assert "{seed}" in joined


def test_workers_are_declared_per_variant(registry):
    assert registry.get("razorpay-fixed").workers == 1
    assert registry.get("razorpay-fixed-2w").workers == 2


def test_fixture_modes_are_all_registered(registry):
    from fixture_modes import MODES

    for mode in MODES:
        profile = registry.get(f"fixture-{mode}")
        assert profile.variant == mode
        assert profile.serve_command[:3] == ("python", "-m", "fixture_modes.serve")


def test_command_formatting(registry, tmp_path):
    profile = registry.get("razorpay-fixed")
    command = profile.format_command(
        profile.serve_command,
        db=tmp_path / "app.db",
        seed=tmp_path / "seed.json",
        host="127.0.0.1",
        port=12345,
    )
    assert command[0:3] == ["python", "-m", "razorpay_ref.serve"]
    assert "--port" in command
    assert command[command.index("--port") + 1] == "12345"
    assert command[command.index("--workers") + 1] == "1"