"""Config shared by the variant apps, resolved from the target-contract env."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_path: str
    provider_base_url: str
    secret: str
    instance: str

    @classmethod
    def from_env(cls) -> "Config":
        try:
            return cls(
                db_path=os.environ["DATABASE_PATH"],
                provider_base_url=os.environ["PROVIDER_BASE_URL"],
                secret=os.environ["WEBHOOK_SECRET"],
                instance=os.environ["DURESS_INSTANCE"],
            )
        except KeyError as exc:
            raise RuntimeError(f"missing required environment variable {exc.args[0]}") from None

    @classmethod
    def build(
        cls,
        *,
        db_path: str | None = None,
        provider_base_url: str | None = None,
        secret: str | None = None,
        instance: str | None = None,
    ) -> "Config":
        """Explicit values (tests) with env fallback for anything omitted."""
        env = cls.from_env() if any(v is None for v in (db_path, provider_base_url, secret, instance)) else None
        return cls(
            db_path=db_path if db_path is not None else env.db_path,
            provider_base_url=provider_base_url if provider_base_url is not None else env.provider_base_url,
            secret=secret if secret is not None else env.secret,
            instance=instance if instance is not None else env.instance,
        )
