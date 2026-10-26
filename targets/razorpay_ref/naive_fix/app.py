"""ASGI entry point: ``razorpay_ref.naive_fix.app:app``."""

from .handler import create_app

app = create_app()
