"""Data cleaning and feature engineering for used car price prediction.

Functions here are imported by notebooks and by `src/train.py`. Keeping them in
a module (instead of inline in notebooks) means the same cleaning logic runs at
training time and at inference time — preventing train/serve skew.
"""

from __future__ import annotations

import pandas as pd

PRICE_LOW_QUANTILE = 0.005
PRICE_HIGH_QUANTILE = 0.995
MAX_REASONABLE_KM = 300_000

DEDUP_KEYS = ["brand", "model", "vehicle_age", "km_driven", "selling_price"]

CATEGORICAL_COLS = [
    "brand",
    "model",
    "seller_type",
    "fuel_type",
    "transmission_type",
]

NUMERIC_COLS = [
    "vehicle_age",
    "km_driven",
    "mileage",
    "engine",
    "max_power",
    "seats",
    "km_per_year",
    "power_per_cc",
]

FEATURE_COLS = CATEGORICAL_COLS + NUMERIC_COLS
TARGET_COL = "selling_price"


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the leftover index col, trim price + km outliers, dedupe near-duplicates.

    Returns a new DataFrame (does not mutate the input).
    """
    out = df.copy()

    if "Unnamed: 0" in out.columns:
        out = out.drop(columns=["Unnamed: 0"])

    low = out[TARGET_COL].quantile(PRICE_LOW_QUANTILE)
    high = out[TARGET_COL].quantile(PRICE_HIGH_QUANTILE)
    out = out[(out[TARGET_COL] >= low) & (out[TARGET_COL] <= high)]

    out = out[out["km_driven"] <= MAX_REASONABLE_KM]

    out = out.drop_duplicates(subset=DEDUP_KEYS, keep="first")

    return out.reset_index(drop=True)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derive `km_per_year` and `power_per_cc`. Safe against zero values."""
    out = df.copy()
    out["km_per_year"] = out["km_driven"] / out["vehicle_age"].clip(lower=1)
    out["power_per_cc"] = out["max_power"] / out["engine"].clip(lower=1)
    return out


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Full pipeline: clean + feature-engineer. Call this at training AND inference."""
    return add_features(clean_dataframe(df))
