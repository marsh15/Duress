"""ASGI entry point: ``razorpay_ref.vulnerable.app:app``."""

from .handler import create_app

app = create_app()
