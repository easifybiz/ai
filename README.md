# VahanOne AI — Car Inspection Service

This is the **AI microservice** for VahanOne. It handles:
- **Damage detection** — YOLOv11x-seg model detects cracks, dents, scratches, glass damage, lamp damage, and flat tyres
- **Price estimation** — CatBoost quantile regression models predict repair cost (low / mid / high)

This service is called by the **vahanone-main** backend via HTTP. It is not used directly by the frontend.

---

## Prerequisites

- **Python 3.12+** and pip
- **Model files** (shared separately via WhatsApp — see below)

---

## 1. Install Dependencies

```bash
pip install -r requirements-api.txt
```

---

## 2. Place Model Files

The large model files cannot be stored on GitHub. They are shared separately via WhatsApp.

Place them in the `artifacts/` folder like this:

```
artifacts/
├── damage_yolo.pt       ← YOLOv11x-seg damage detection model (125 MB)
├── model_p10.cbm        ← CatBoost low price estimate model
├── model_p50.cbm        ← CatBoost mid price estimate model
├── model_p90.cbm        ← CatBoost high price estimate model
└── vocab.json           ← already present in repo
```

---

## 3. Start the Service

```bash
# Runs on port 8080
uvicorn api:app --host 0.0.0.0 --port 8080
```

The service will be available at `http://127.0.0.1:8080`.

---

## 4. Connect to vahanone-main Backend

In your `vahanone-main/backend/.env`, set:

```
HF_SPACES_URL=http://127.0.0.1:8080
```

The backend will automatically route AI inspection requests to this local service.

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/predict` | POST | Price estimation from car specs |
| `/api/detect-damage` | POST | Damage detection from car image |
| `/api/vocab` | GET | Supported car brands and models |
| `/` | GET | Gradio UI (visual testing) |

---

## Damage Detection Classes

The YOLO model detects 6 damage types:

| Class | Description |
|---|---|
| `crack` | Structural cracks in body panels |
| `dent` | Dents and deformations |
| `glass_damage` | Cracked or shattered glass |
| `lamp_damage` | Broken headlamps or tail lamps |
| `scratch` | Surface scratches and paint damage |
| `tire_damage` | Flat or damaged tyres |

---

## Price Estimation

The CatBoost models use quantile regression to return three price estimates:
- **low** (p10) — conservative lower bound
- **mid** (p50) — median estimate
- **high** (p90) — upper bound

Input fields: brand, model, vehicle age, km driven, fuel type, transmission, seller type, engine CC, max power, mileage, seats.
