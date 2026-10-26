"""ASGI entry point: ``razorpay_ref.fixed.app:app``."""

from .handler import create_app

app = create_app()