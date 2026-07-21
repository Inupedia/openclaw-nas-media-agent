"""Deployment tests without shadowing the product's ``deploy`` namespace."""

from pathlib import Path

_PRODUCT_DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
if str(_PRODUCT_DEPLOY) not in __path__:
    __path__.append(str(_PRODUCT_DEPLOY))
