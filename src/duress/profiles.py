"""Target profiles: contract v1 declarations loaded from ``targets/*/profile.toml``.

F1 lives here: the profile declares the real launch command (``razorpay_ref.serve``
for the reference target), and the supervisor formats that command. No engine
component invokes stock ``uvicorn``.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import SeedDocument


@dataclass(frozen=True)
class SigningProfile:
    scheme: str
    header: str
    event_id_header: str
    secret_ref: str


@dataclass(frozen=True)
class TargetProfile:
    target_id: str
    variant: str
    profile_id: str
    domain: str
    contract_version: int
    workers: int
    app: str
    seed_path: Path
    roles: dict[str, str]
    signing: SigningProfile
    init_command: tuple[str, ...]
    serve_command: tuple[str, ...]
    cwd: Path

    def format_command(
        self,
        command: tuple[str, ...],
        *,
        db: Path,
        seed: Path,
        host: str,
        port: int,
    ) -> list[str]:
        values = {
            "db": str(db),
            "seed": str(seed),
            "host": host,
            "port": str(port),
            "workers": str(self.workers),
            "instance": "",
        }
        return [part.format(**values) for part in command]

    @property
    def target_dir(self) -> Path:
        return self.seed_path.parent


class ProfileRegistry:
    def __init__(self, targets: dict[str, TargetProfile], root: Path) -> None:
        self._targets = targets
        self.root = root

    @classmethod
    def discover(cls, root: Path | None = None) -> "ProfileRegistry":
        root = Path(root or repo_root()).resolve()
        profile_paths = sorted(root.glob("targets/*/profile.toml"))
        # Workspace callers may pass the process cwd explicitly. If that
        # directory is not a Duress checkout, use the bundled reference tree
        # found by repo_root instead of returning an empty registry.
        if not profile_paths:
            fallback = repo_root(root)
            if fallback != root:
                bundled_paths = sorted(fallback.glob("targets/*/profile.toml"))
                if bundled_paths:
                    root = fallback
                    profile_paths = bundled_paths
        targets: dict[str, TargetProfile] = {}
        for profile_path in profile_paths:
            data = tomllib.loads(profile_path.read_text())
            profile = data["profile"]
            process = data.get("process", {})
            seed_spec = data["seed"]
            signing = data["signing"]
            for variant_name, variant in data.get("variants", {}).items():
                target_id = variant.get("id") or f"{profile['id']}-{variant_name}"
                if "init" not in variant or "serve" not in variant:
                    raise ValueError(
                        f"{profile_path}: variant {variant_name!r} must declare init and serve"
                    )
                targets[target_id] = TargetProfile(
                    target_id=target_id,
                    variant=variant_name,
                    profile_id=profile["id"],
                    domain=profile["domain"],
                    contract_version=int(profile["contract_version"]),
                    workers=int(variant.get("workers", process.get("workers", 1))),
                    app=variant["app"],
                    seed_path=(profile_path.parent / seed_spec["path"]).resolve(),
                    roles=dict(seed_spec.get("roles", {})),
                    signing=SigningProfile(
                        scheme=signing["scheme"],
                        header=signing["header"],
                        event_id_header=signing["event_id_header"],
                        secret_ref=signing["secret_ref"],
                    ),
                    init_command=tuple(variant["init"]),
                    serve_command=tuple(variant["serve"]),
                    cwd=root,
                )
        return cls(targets, root)

    def get(self, target_id: str) -> TargetProfile:
        try:
            return self._targets[target_id]
        except KeyError:
            known = ", ".join(sorted(self._targets))
            raise KeyError(f"unknown target {target_id!r}; known: {known}") from None

    def all(self) -> list[TargetProfile]:
        return [self._targets[key] for key in sorted(self._targets)]


def repo_root(start: Path | None = None) -> Path:
    override = os.environ.get("DURESS_ROOT")
    if override:
        return Path(override).resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "targets").is_dir() and (candidate / "pyproject.toml").exists():
            return candidate
    # A wheel keeps the reference profiles, fixtures and recorded showcase
    # under the package.  Prefer an actual checkout above so contributors still
    # execute the live source tree and can edit those resources in place.
    bundled = Path(__file__).resolve().parent / "resources"
    if any(bundled.glob("targets/*/profile.toml")):
        return bundled
    return current


def load_seed(profile: TargetProfile) -> SeedDocument:
    payload: dict[str, Any] = json.loads(profile.seed_path.read_text())
    return SeedDocument(
        orders=list(payload.get("orders", [])),
        payments=list(payload.get("payments", [])),
    )


def payment_rows(seed: SeedDocument) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in seed.payments}


def order_rows(seed: SeedDocument) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in seed.orders}


def captured_fixtures(seed: SeedDocument) -> dict[str, dict[str, Any]]:
    """External truth: every seeded payment is captured at the provider."""
    return {
        row["id"]: {
            "id": row["id"],
            "order_id": row["order_id"],
            "amount": row["amount"],
            "currency": row["currency"],
            "status": "captured",
        }
        for row in seed.payments
    }


def resolve_roles(seed: SeedDocument, roles: dict[str, str]) -> None:
    payment_ids = {row["id"] for row in seed.payments}
    order_ids = {row["id"] for row in seed.orders}
    for role, entity_id in roles.items():
        if role.startswith("payment:") and entity_id not in payment_ids:
            raise ValueError(f"role {role} points at unknown payment {entity_id}")
        if role.startswith("order:") and entity_id not in order_ids:
            raise ValueError(f"role {role} points at unknown order {entity_id}")
