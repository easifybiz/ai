# CLAUDE.md — Project Context

> Read this file first before doing anything in this project. It encodes the decisions that have already been made and the ones that are explicitly out of scope. Do not re-debate them without a clear new reason from the user.

---

## What This Project Is

A **used car price prediction tool for the Indian market**, built for a single-founder Indian used-car buy/sell/servicing business. The long-term goal is a public website widget where a visitor enters car details + uploads photos → gets a price range adjusted for visible damage. Non-transactional lead-gen tool, not a binding quote.

Be opinionated, give one recommendation per choice, explain key terms without being condescending. Don't bury decisions in a list of 10 alternatives.

---

## Current Phase

**Phase 1 of 3 — Base pricing engine + Gradio demo.**

Phase 1 deliverable: a working price predictor in a Gradio UI with a public `gradio.live` URL, shown to the client in a screen-share meeting. No deployment, no website, no images.

**Phase 2** (visual damage detection with YOLO) and **Phase 3** (FastAPI + Docker + public website) are triggered only after the client approves Phase 1. They get their own planning passes — do not scope them preemptively.

---

## Locked Decisions (Do Not Re-Litigate)

### Dataset
- **Use `data/cardekho_dataset.csv` only.** 15,411 rows. Brand/model already split. Numeric engine/max_power/mileage/seats.
- `car data.csv` is a data leak (contains `Present_Price` ≈ the answer). Ignored.
- `dataset1.csv` and `dataset2.csv` are older (~2020) CarDekho scrapes. Combining them rejected for v1 — schema harmonization, dedup, and price-era mixing aren't worth the marginal accuracy gain.

### Model
- **CatBoost Regressor.** Not XGBoost. Native high-cardinality categorical handling is the deciding factor.
- **Target = `log1p(selling_price)`.** Raw-price training destroys budget-car accuracy due to 100× price range.
- **Three quantile models** at α = 0.1, 0.5, 0.9 for [low, mid, high] output. Not a single point estimate.

### Features accepted as missing in v1
- No city/location.
- No owner count. (Dataset doesn't have it. Cost: ~2–3% accuracy. Acceptable for "decent" v1.)
- No explicit variant. (Model column is truncated, e.g. "Swift" not "Swift ZXI+".) Variant signal partially recovered via `max_power`, `transmission_type`, `fuel_type`. Residual variance absorbed into the quantile interval.

### Dev environment
- **VS Code locally with Jupyter extension.** Not Colab.
- Python 3.12 in `.venv` virtualenv (Homebrew `python3.12`).
- CatBoost on 15K rows trains in ~1–2 min on laptop CPU. No GPU needed.
- Gradio `launch(share=True)` gives the client demo URL from localhost.

### Accuracy bar
- **Aim**: R² 0.85–0.92, MAPE 12–18%.
- **Ship**: ~80% of predictions within 15% of fair market value.
- **Red flag**: R² > 0.97 means data leak. R² < 0.80 or MAPE > 25% means something is broken.

---

## Explicitly Out of Scope for Phase 1

- Image upload, damage detection, computer vision of any kind
- FastAPI, Docker, any hosting
- Website widget or frontend code
- City-level pricing
- Owner count
- Variant-level prediction
- Automated retraining, cron jobs, CI
- Paid APIs or paid data
- Deep learning of any flavor (transformers, neural nets for tabular)

If the user asks for any of these, confirm it's a Phase 2/3 topic before starting work.

---

## Key Terms

- **Pipeline**: a single sklearn `Pipeline` object that bundles all preprocessing steps. Fit once on training data, pickle it, load at inference. Ensures train-time and inference-time preprocessing are identical — prevents the #1 deployment bug.
- **Preprocessor**: the part of the pipeline that turns raw user input (`{"brand": "Maruti", "model": "Swift", ...}`) into the numeric/categorical array the model expects.
- **Artifacts**: saved files that carry all trained intelligence to production. In this project: `preprocessor.pkl`, `model_p10.cbm`, `model_p50.cbm`, `model_p90.cbm`, `vocab.json`.
- **Vocab**: a JSON file listing valid brands and models. The website dropdowns read it; the API validates requests against it.
- **Quantile regression**: instead of predicting one number, predict "the value where 10% of similar data falls below" (P10), the median (P50), and the 90th percentile (P90). Gives an honest range.
- **log1p / expm1**: `log(1 + x)` and its inverse. Used to compress price range during training; inverted at inference.

---

## File Layout

```
aicarinspection/
  data/cardekho_dataset.csv             # source data
  notebooks/01_explore_clean.ipynb      # EDA + cleaning
  notebooks/02_train_models.ipynb       # training
  notebooks/03_gradio_demo.ipynb        # client demo
  src/features.py                       # clean + feature-engineer functions
  src/train.py                          # training script, importable
  src/inference.py                      # load artifacts + predict()
  artifacts/                            # saved models + vocab
  requirements.txt
  CLAUDE.md                             # this file
  
```

---

## How to Work With This User

- Opinionated. One recommendation per choice. Flag bad ideas even if asked for.
- Explain key terms briefly when they come up. Don't assume prior ML deployment knowledge.
- Prefer small, verified steps over big commits. Each `IMPLEMENTATION.md` step has a "done when" gate — stop and verify before moving on.
- When the user asks "does X work?" — check the actual data/code, don't guess.
- Do not expand scope. Phase 1 ships Phase 1 only.

---

## References

- [PLAN.md](./PLAN.md) — full project plan and context
- [IMPLEMENTATION.md](./IMPLEMENTATION.md) — step-by-step execution checklist
