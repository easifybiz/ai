"""FastAPI + Gradio combined service for HF Spaces deployment.

- /api/predict, /api/detect-damage, /api/vocab → JSON API (for VahanOne backend)
- / → Standalone Gradio UI (input fields + submit, no RC fetch)
"""

from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from typing import Any

import numpy as np

import gradio as gr
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel, Field
# added by Swati on 28062026 for yolov8 COCO Model for detecting vehicles
from ultralytics import YOLO 

from src.damage import CATEGORY_HUMAN, detect_damage
from src.inference import (
    DAMAGE_DEDUCTION_PCT,
    MAX_DAMAGE_DEDUCTION_PCT,
    _VOCAB as VOCAB,
    apply_damage_discount,
    format_inr,
    predict,
)

# ── Shared vocab ──────────────────────────────────────────────────────────────

BRANDS = sorted(VOCAB["brand_to_models"].keys())
FUEL_TYPES = VOCAB["fuel_types"]
TRANSMISSIONS = VOCAB["transmissions"]
SELLER_TYPES = VOCAB["seller_types"]
DEFAULTS_LOOKUP = {
    (row["brand"], row["model"]): row for row in VOCAB["defaults_by_model"]
}

DEFAULT_BRAND = "Maruti" if "Maruti" in BRANDS else BRANDS[0]
_maruti_models = VOCAB["brand_to_models"][DEFAULT_BRAND]
DEFAULT_MODEL = "Swift" if "Swift" in _maruti_models else _maruti_models[0]
DEFAULT_SPECS = DEFAULTS_LOOKUP[(DEFAULT_BRAND, DEFAULT_MODEL)]
CURRENT_YEAR = datetime.now().year

# added by Swati on 28062026 for yolov8 COCO Model for detecting vehicles
VEHICLE_MODEL = YOLO("artifacts/yolov8n.pt")

COCO_VEHICLE_CLASSES = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

# ── FastAPI (JSON API) ────────────────────────────────────────────────────────

app = FastAPI(title="VahanOne Car Price Estimator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api = FastAPI(title="VahanOne Car Price Estimator API", version="1.0.0")


class PredictRequest(BaseModel):
    brand: str
    model: str
    vehicle_age: int = Field(ge=0, le=50)
    km_driven: int = Field(ge=0, le=500_000)
    fuel_type: str
    transmission_type: str
    seller_type: str
    engine: int = Field(ge=100, le=10_000)
    max_power: float = Field(ge=1, le=1000)
    mileage: float = Field(ge=1, le=100)
    seats: int = Field(ge=1, le=15)


class PredictResponse(BaseModel):
    low: int | None = None
    mid: int | None = None
    high: int | None = None
    model_version: str
    fallback: str | None = None
    estimate: int | None = None
    reason: str | None = None
    message: str | None = None

# added by swati mishra on 01072026 for damage inspection report
class DamageResponse(BaseModel):
    ok: bool
    message: str
    categories_detected: list[str]
    raw_class_counts: dict[str, int]
    deduction_pct: int
    annotated_image: str | None = None
    severity: str | None = None 
    confidence: float = 0.0
    damage_location: str | None = None
    damage_items: list[dict[str, Any]] = []
    repair_cost_category: str | None = None
    inspection_score: int | None = None
    recommendation: str | None = None

# added by Swati on 28062026 for yolov8 COCO Model for detecting vehicles
class VehicleValidationResponse(BaseModel):
    valid_image: bool
    is_vehicle: bool
    vehicle_type: str | None = None
    issue_type: str
    confidence: float = 0.0
    reason: str


# added by Swati on 28062026 for yolov8 COCO Model for detecting vehicles
def validate_vehicle_local(img: Image.Image) -> dict[str, Any]:
    results = VEHICLE_MODEL(img, verbose=False)

    best = {
        "class": None,
        "confidence": 0.0,
    }

    for r in results:
        if r.boxes is None:
            continue

        for box in r.boxes:
            cls = int(box.cls[0])
            conf = float(box.conf[0])

            if cls in COCO_VEHICLE_CLASSES and conf > best["confidence"]:
                best = {
                    "class": COCO_VEHICLE_CLASSES[cls],
                    "confidence": conf,
                }

    if best["class"] == "car" and best["confidence"] >= 0.55:
        return {
            "valid_image": True,
            "is_vehicle": True,
            "vehicle_type": "car",
            "issue_type": "valid_car",
            "confidence": best["confidence"],
            "reason": "Car detected locally",
        }

    if best["class"] and best["class"] != "car" and best["confidence"] >= 0.55:
        return {
            "valid_image": False,
            "is_vehicle": True,
            "vehicle_type": best["class"],
            "issue_type": "not_car",
            "confidence": best["confidence"],
            "reason": "Vehicle detected, but not a car",
        }

    return {
        "valid_image": False,
        "is_vehicle": False,
        "vehicle_type": best["class"] or "none",
        "issue_type": "uncertain",
        "confidence": best["confidence"],
        "reason": "No confident car detection",
    }

@api.get("/health")
def health():
    return {"status": "ok", "model_version": "v1.0"}


@api.get("/vocab")
def get_vocab() -> dict[str, Any]:
    return {
        "brand_to_models": VOCAB["brand_to_models"],
        "fuel_types": VOCAB["fuel_types"],
        "transmissions": VOCAB["transmissions"],
        "seller_types": VOCAB["seller_types"],
        "defaults_by_model": VOCAB["defaults_by_model"],
    }


@api.post("/predict", response_model=PredictResponse)
def predict_price(req: PredictRequest):
    return predict(req.model_dump())

# added by Swati on 28062026 for yolov8 COCO Model for detecting vehicles
@api.post("/validate-vehicle", response_model=VehicleValidationResponse)
async def validate_vehicle(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Upload must be an image file")

    data = await file.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")

    return validate_vehicle_local(img)


@api.post("/detect-damage", response_model=DamageResponse)
async def damage_detect(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Upload must be an image file")

    data = await file.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    det = detect_damage(img)

    pct = sum(DAMAGE_DEDUCTION_PCT.get(c, 0) for c in det["categories_detected"])
    pct = min(pct, MAX_DAMAGE_DEDUCTION_PCT)

    annotated_b64 = None
    if det["ok"] and det.get("annotated_image") is not None:
        ann_img = Image.fromarray(np.asarray(det["annotated_image"]).astype("uint8"))
        buf = io.BytesIO()
        ann_img.save(buf, format="JPEG", quality=80)
        annotated_b64 = base64.b64encode(buf.getvalue()).decode()
    
# added by swati mishra on 01072026 for damage inspection report    
    return {
        "ok": det["ok"],
        "message": det["message"],
        "categories_detected": det["categories_detected"],
        "raw_class_counts": det["raw_class_counts"],
        "deduction_pct": pct,
        "annotated_image": annotated_b64,
        "severity": det.get("severity"),
        "confidence": det.get("confidence", 0.0),
        "damage_location": det.get("damage_location"),
        "damage_items": det.get("damage_items", []),
        "repair_cost_category": det.get("repair_cost_category"),
        "inspection_score": det.get("inspection_score"),
        "recommendation": det.get("recommendation"),
    }


# ── Gradio standalone UI ─────────────────────────────────────────────────────


def models_for_brand(brand, current_model=None):
    if not brand:
        return gr.update(choices=[], value=None)
    models = VOCAB["brand_to_models"].get(brand, [])
    if current_model in models:
        return gr.update(choices=models, value=current_model)
    return gr.update(choices=models, value=models[0] if models else None)


def fill_defaults(brand, model):
    d = DEFAULTS_LOOKUP.get((brand, model))
    if not d:
        return gr.update(), gr.update(), gr.update(), gr.update()
    return (
        gr.update(value=int(d["engine"])),
        gr.update(value=float(d["max_power"])),
        gr.update(value=float(d["mileage"])),
        gr.update(value=int(d["seats"])),
    )

def build_summary_md(adj_result):
    if adj_result.get("estimate") is None and adj_result.get("reason") == "insufficient_data":
        return adj_result["message"]

    lo = format_inr(adj_result["low"])
    mid = format_inr(adj_result["mid"])
    hi = format_inr(adj_result["high"])
    lines = [f"### {lo} – {hi}  *(est. {mid})*"]

    if adj_result.get("damage_detected"):
        cats_human = [CATEGORY_HUMAN.get(c, c) for c in adj_result["damage_categories"]]
        spec = adj_result["spec_based_price"]
        lines.append(
            f"\n⚠️ **Visible damage detected** — estimate reduced by "
            f"**{adj_result['discount_pct']}%** from the spec-based price ({format_inr(spec['mid'])})."
        )
        lines.append(f"\nDetected: {', '.join(cats_human)}")
    elif adj_result.get("damage_detected") is False:
        lines.append("\n✅ **No visible damage detected** — estimate stands.")

    return "\n".join(lines)


def estimate(
    brand, model, reg_year, km_driven, fuel_type, transmission_type,
    seller_type, engine, max_power, mileage, seats, owner_count, car_image,
):
    vehicle_age = max(0, CURRENT_YEAR - int(reg_year))
    payload = {
        "brand": brand, "model": model,
        "vehicle_age": vehicle_age, "km_driven": int(km_driven),
        "fuel_type": fuel_type, "transmission_type": transmission_type,
        "seller_type": seller_type, "engine": int(engine),
        "max_power": float(max_power), "mileage": float(mileage),
        "seats": int(seats),
    }
    spec_result = predict(payload)

    annotated = None
    damage_info: dict[str, Any] = {"image_provided": False}
    if car_image is not None:
        det = detect_damage(car_image)
        damage_info = {
            "image_provided": True,
            "image_ok": det["ok"],
            "message": det["message"],
            "categories_detected": det["categories_detected"],
            "raw_class_counts": det["raw_class_counts"],
        }
        if det["ok"]:
            annotated = det["annotated_image"]
            adj_result = apply_damage_discount(spec_result, det["categories_detected"])
        else:
            adj_result = spec_result
    else:
        adj_result = spec_result

    summary_md = build_summary_md(adj_result)
    if damage_info.get("image_provided") and not damage_info.get("image_ok"):
        summary_md += f"\n\nℹ️ Image issue: {damage_info['message']}"

    debug = {
        **adj_result,
        "owner_count": owner_count,
        "reg_year": int(reg_year),
        "vehicle_age": vehicle_age,
        "damage": damage_info,
    }
    return summary_md, annotated, json.dumps(debug, indent=2, default=str)


with gr.Blocks(title="VahanOne — AI Car Price Estimator") as demo:
    gr.Markdown("# Used Car Price Estimator")
    gr.Markdown(
        "Fill in the vehicle details below and optionally upload a photo for damage assessment."
    )

    with gr.Row():
        with gr.Column():
            brand = gr.Dropdown(choices=BRANDS, label="Brand", value=DEFAULT_BRAND)
            model = gr.Dropdown(
                choices=VOCAB["brand_to_models"][DEFAULT_BRAND],
                label="Model",
                value=DEFAULT_MODEL,
            )
            reg_year = gr.Number(
                value=CURRENT_YEAR - 5, precision=0,
                label=f"Year of registration (current year: {CURRENT_YEAR})",
            )
            km_driven = gr.Number(value=50000, label="Kilometres driven", precision=0)

        with gr.Column():
            fuel_type = gr.Dropdown(choices=FUEL_TYPES, label="Fuel type", value="Petrol")
            transmission_type = gr.Dropdown(choices=TRANSMISSIONS, label="Transmission", value="Manual")
            seller_type = gr.Dropdown(choices=SELLER_TYPES, label="Seller type", value="Individual")
            owner_count = gr.Dropdown(
                choices=["1st owner", "2nd owner", "3rd owner", "4th+ owner"],
                value="1st owner", label="Number of previous owners",
            )

        with gr.Column():
            engine = gr.Number(value=int(DEFAULT_SPECS["engine"]), label="Engine (CC)", precision=0)
            max_power = gr.Number(value=float(DEFAULT_SPECS["max_power"]), label="Max power (bhp)")
            mileage = gr.Number(value=float(DEFAULT_SPECS["mileage"]), label="Mileage (kmpl)")
            seats = gr.Number(value=int(DEFAULT_SPECS["seats"]), label="Seats", precision=0)

    gr.Markdown("### Optional: upload a car photo for damage detection")
    car_image = gr.Image(label="Car photo", type="pil", height=320)

    submit_btn = gr.Button("Estimate price", variant="primary")

    with gr.Row():
        with gr.Column():
            output_summary = gr.Markdown(label="Price estimate")
        with gr.Column():
            output_annotated = gr.Image(label="Detected damage (annotated)", type="numpy", height=320)

    with gr.Accordion("Raw JSON (for debugging)", open=False):
        output_json = gr.Code(label="")

    brand.change(models_for_brand, inputs=[brand, model], outputs=model)
    brand.change(fill_defaults, inputs=[brand, model], outputs=[engine, max_power, mileage, seats])
    model.change(fill_defaults, inputs=[brand, model], outputs=[engine, max_power, mileage, seats])

    submit_btn.click(
        estimate,
        inputs=[
            brand, model, reg_year, km_driven, fuel_type, transmission_type,
            seller_type, engine, max_power, mileage, seats, owner_count, car_image,
        ],
        outputs=[output_summary, output_annotated, output_json],
    )

    gr.Markdown(
        "---\nEstimate is automated and indicative only — actual price depends on physical inspection."
    )

# Mount API sub-app (isolated so Gradio doesn't introspect its schemas)
app.mount("/api", api)

# Mount Gradio on FastAPI
app = gr.mount_gradio_app(app, demo, path="/")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)
