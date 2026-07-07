"""Inference: load the three quantile CatBoost models and serve a price range.

Used by the Gradio demo (Phase 1) and will be reused by the FastAPI endpoint
in Phase 3 without modification. Keeps prediction logic out of notebooks and
away from UI code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from src.features import (
    FEATURE_COLS,
    add_features,
)

ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "artifacts"
MODEL_VERSION = "v1.0"
QUANTILE_FILES = {
    "low":  "model_p10.cbm",
    "mid":  "model_p50.cbm",
    "high": "model_p90.cbm",
}


def _load_models() -> dict[str, CatBoostRegressor]:
    models = {}
    for key, fname in QUANTILE_FILES.items():
        path = ARTIFACT_DIR / fname
        if not path.exists():
            raise FileNotFoundError(
                f"Missing artifact: {path}. Run notebooks/02_train_models.ipynb first."
            )
        m = CatBoostRegressor()
        m.load_model(str(path))
        models[key] = m
    return models


def _load_vocab() -> dict[str, Any]:
    path = ARTIFACT_DIR / "vocab.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing vocab: {path}")
    with open(path) as f:
        return json.load(f)


# Module-level singletons — load once per process.
_MODELS = _load_models()
_VOCAB = _load_vocab()


def _round_to(n: float, step: int = 1000) -> int:
    """Round to nearest `step` rupees for display."""
    return int(round(n / step) * step)


def predict(payload: dict[str, Any]) -> dict[str, Any]:
    """Predict a price range for one car.

    Required keys in payload:
      brand, model, vehicle_age, km_driven, seller_type, fuel_type,
      transmission_type, mileage, engine, max_power, seats.

    Returns one of:
      - {"low", "mid", "high", "model_version", "fallback": None}
      - {"low", "mid", "high", "model_version", "fallback": "brand_level"}
      - {"estimate": None, "reason": "insufficient_data", "message": ...}
    """
    brand = payload.get("brand")
    model = payload.get("model")

    if brand not in _VOCAB["brand_to_models"]:
        return {
            "estimate": None,
            "reason": "insufficient_data",
            "message": f"We don't have enough listings for brand '{brand}' to give a reliable estimate.",
            "model_version": MODEL_VERSION,
        }

    known_models = _VOCAB["brand_to_models"][brand]
    fallback = None
    if model not in known_models:
        fallback = "brand_level"

    row = pd.DataFrame([{c: payload.get(c) for c in FEATURE_COLS}])
    row = add_features(row.drop(columns=["km_per_year", "power_per_cc"], errors="ignore"))

    preds_log = {k: m.predict(row)[0] for k, m in _MODELS.items()}
    preds = {k: float(np.expm1(v)) for k, v in preds_log.items()}

    if fallback == "brand_level":
        mid = preds["mid"]
        spread = max(preds["high"] - preds["low"], mid * 0.25)
        preds["low"]  = mid - spread * 0.75
        preds["high"] = mid + spread * 0.75

    low, mid, high = sorted([preds["low"], preds["mid"], preds["high"]])

    return {
        "low":  _round_to(low),
        "mid":  _round_to(mid),
        "high": _round_to(high),
        "model_version": MODEL_VERSION,
        "fallback": fallback,
    }


# Damage → price-deduction tiers. Applied ONCE per category (not per instance),
# so 5 dents on the same car still count as one "dent" deduction. Aligns with how
# Indian used-car dealers (Spinny / Cars24) actually price visible damage.
DAMAGE_DEDUCTION_PCT = {
    "glass_damage": 8,   # shattered windscreen — major
    "lamp_damage":  6,   # broken head/taillamp — major
    "dent":         5,   # panel dent — moderate
    "tire_damage":  4,   # flat / damaged tire — replaceable cost
    "crack":        4,   # panel or glass crack — moderate
    "scratch":      2,   # cosmetic — minor
}
MAX_DAMAGE_DEDUCTION_PCT = 20  # Hard cap regardless of how much damage is detected.


def apply_damage_discount(
    price_result: dict[str, Any], categories_detected: list[str]
) -> dict[str, Any]:
    """Adjust a spec-based price range downward based on detected damage categories.

    Returns a NEW dict with adjusted low/mid/high plus damage_summary fields.
    If price_result has no numeric estimate (insufficient_data), it's returned as-is.
    """
    if price_result.get("estimate") is None and price_result.get("reason") == "insufficient_data":
        return price_result

    if not categories_detected:
        return {
            **price_result,
            "damage_detected": False,
            "discount_pct": 0,
            "damage_categories": [],
        }

    pct = sum(DAMAGE_DEDUCTION_PCT.get(c, 0) for c in categories_detected)
    pct = min(pct, MAX_DAMAGE_DEDUCTION_PCT)
    multiplier = 1 - pct / 100

    return {
        **price_result,
        "low":  _round_to(price_result["low"] * multiplier),
        "mid":  _round_to(price_result["mid"] * multiplier),
        "high": _round_to(price_result["high"] * multiplier),
        "damage_detected": True,
        "discount_pct": pct,
        "damage_categories": list(categories_detected),
        "spec_based_price": {
            "low":  price_result["low"],
            "mid":  price_result["mid"],
            "high": price_result["high"],
        },
    }


def format_inr(amount: int) -> str:
    """Format ₹1,250,000 as '₹12.5L'."""
    if amount >= 10_000_000:
        return f"₹{amount / 10_000_000:.2f}Cr"
    if amount >= 100_000:
        return f"₹{amount / 100_000:.2f}L"
    return f"₹{amount:,}"


def format_range(result: dict[str, Any]) -> str:
    if result.get("estimate") is None and result.get("reason") == "insufficient_data":
        return result["message"]
    lo = format_inr(result["low"])
    hi = format_inr(result["high"])
    md = format_inr(result["mid"])
    suffix = " (brand-level estimate — model unknown in training data)" if result.get("fallback") == "brand_level" else ""
    return f"{lo} – {hi}  (est. {md}){suffix}"
