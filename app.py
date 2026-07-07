"""Gradio app entrypoint for containerised deployment.

Extracted from notebooks/04_gradio_demo_with_damage.ipynb so it can run as a
plain Python process inside Docker. The notebook stays as the dev/exploration
surface; this file is what production actually runs.
"""

import json
import os
import re
from datetime import datetime

import requests

# Workaround for gradio 5.8.0 + gradio_client API-schema bug
# (TypeError: argument of type 'bool' is not iterable in get_type).
import gradio_client.utils as _gcu
_orig_get_type = _gcu.get_type
def _safe_get_type(schema):
    if isinstance(schema, bool):
        return 'Any'
    return _orig_get_type(schema)
_gcu.get_type = _safe_get_type

_orig_json_schema_to_python_type = _gcu._json_schema_to_python_type
def _safe_json_schema_to_python_type(schema, defs=None):
    if isinstance(schema, bool):
        return 'Any'
    return _orig_json_schema_to_python_type(schema, defs)
_gcu._json_schema_to_python_type = _safe_json_schema_to_python_type

import gradio as gr
from src.inference import predict, format_inr, apply_damage_discount, _VOCAB as VOCAB
from src.damage import detect_damage, CATEGORY_HUMAN

BRANDS = sorted(VOCAB['brand_to_models'].keys())
FUEL_TYPES = VOCAB['fuel_types']
TRANSMISSIONS = VOCAB['transmissions']
SELLER_TYPES = VOCAB['seller_types']
DEFAULTS_LOOKUP = {(row['brand'], row['model']): row for row in VOCAB['defaults_by_model']}

DEFAULT_BRAND = 'Maruti' if 'Maruti' in BRANDS else BRANDS[0]
_maruti_models = VOCAB['brand_to_models'][DEFAULT_BRAND]
DEFAULT_MODEL = 'Swift' if 'Swift' in _maruti_models else _maruti_models[0]
DEFAULT_SPECS = DEFAULTS_LOOKUP[(DEFAULT_BRAND, DEFAULT_MODEL)]

CURRENT_YEAR = datetime.now().year
DEFAULT_REG_YEAR = CURRENT_YEAR - 5
MOBILE_RE = re.compile(r'^(?:\+?91)?[6-9]\d{9}$')

BACKEND_URL = os.environ.get('BACKEND_URL', 'http://localhost:8000/easifybizsvc').rstrip('/')
RC_NUMBER_RE = re.compile(r'^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$')

# Map ULIP fuel type strings → CatBoost vocab fuel types
FUEL_NORMALISE = {
    'PETROL': 'Petrol', 'DIESEL': 'Diesel', 'CNG': 'CNG', 'LPG': 'LPG',
    'ELECTRIC': 'Electric', 'PETROL/CNG': 'CNG', 'PETROL/LPG': 'LPG',
}


def normalise_brand(ulip_maker_name):
    """ULIP returns "HYUNDAI MOTOR INDIA LTD" — match first word against vocab brands."""
    if not ulip_maker_name:
        return None
    first = ulip_maker_name.strip().split()[0].title()
    for b in BRANDS:
        if b.lower() == first.lower():
            return b
    # Try whole-name substring match for compound brands like "MAHINDRA & MAHINDRA"
    for b in BRANDS:
        if b.lower() in ulip_maker_name.lower():
            return b
    return None


def normalise_model(brand, ulip_maker_model):
    """ULIP returns "ACCENT GLS" — match against vocab models for that brand."""
    if not brand or not ulip_maker_model:
        return None
    models = VOCAB['brand_to_models'].get(brand, [])
    # Try exact match on first word, then any vocab model that's a substring
    first = ulip_maker_model.strip().split()[0].title()
    for m in models:
        if m.lower() == first.lower():
            return m
    for m in models:
        if m.lower() in ulip_maker_model.lower():
            return m
    return None


def normalise_fuel(ulip_fuel):
    if not ulip_fuel:
        return None
    key = ulip_fuel.strip().upper()
    return FUEL_NORMALISE.get(key)


def normalise_owner_count(serial_no):
    """ULIP owner_serial_no: 0=1st owner, 1=2nd, etc. Cap at 4+."""
    if serial_no is None:
        return None
    try:
        n = int(serial_no)
    except (TypeError, ValueError):
        return None
    labels = ['1st owner', '2nd owner', '3rd owner', '4th+ owner']
    return labels[min(n, 3)]


def parse_reg_year(reg_date):
    """ULIP registration_date: '25-May-2006' → 2006."""
    if not reg_date:
        return None
    m = re.search(r'(19|20)\d{2}', reg_date)
    return int(m.group()) if m else None


def fetch_rc_details(rc_number):
    """Hit backend RC endpoint, normalise the response, return values for all auto-fillable Gradio fields.

    Returns a tuple of gr.update() objects matching the order:
      (status_md, brand, model, reg_year, fuel_type, engine, seats, owner_count)
    """
    if not rc_number or not rc_number.strip():
        return (gr.update(value='⚠️ Enter a registration number first.'),
                gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

    rc = rc_number.strip().upper().replace(' ', '')
    if not RC_NUMBER_RE.match(rc):
        return (gr.update(value=f'⚠️ "{rc}" doesn\'t look like a valid registration number (e.g. MH12DE1234).'),
                gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

    try:
        # forceFlag=True so the backend hits ULIP even when the vehicle isn't already cached
        # in our DB. Without this, fresh RC numbers always come back as "not found".
        resp = requests.post(
            f'{BACKEND_URL}/rc_details',
            json={'vehicleNumber': rc, 'forceFlag': True, 'userId': 1},
            timeout=60,
        )
    except requests.RequestException as e:
        return (gr.update(value=f'⚠️ Network error contacting RC service: {e}'),
                gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

    if resp.status_code != 200:
        # Try to surface the backend's clean detail message; otherwise fall back per status code.
        try:
            detail = (resp.json() or {}).get('detail', '')
        except ValueError:
            detail = ''
        if resp.status_code == 404:
            # Not-found is informational, not an error — don't alarm the user.
            msg = f'ℹ️ No record found for **{rc}** in the RC database. Please double-check the registration number and try again.'
        elif resp.status_code == 503:
            msg = '⚠️ Government RC service is slow right now. Please try again in a few seconds.'
        elif isinstance(detail, str) and detail:
            msg = f'⚠️ {detail}'
        else:
            msg = f'⚠️ RC lookup failed (HTTP {resp.status_code}).'
        return (gr.update(value=msg),
                gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

    try:
        data = resp.json().get('data') or {}
    except ValueError:
        return (gr.update(value='⚠️ RC service returned malformed data.'),
                gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

    brand_v = normalise_brand(data.get('maker_name'))
    model_v = normalise_model(brand_v, data.get('maker_model'))
    fuel_v = normalise_fuel(data.get('fuel_type'))
    year_v = parse_reg_year(data.get('registration_date'))
    engine_v = data.get('rc_cubic_cap')
    seats_v = data.get('seat_capacity')
    owner_v = normalise_owner_count(data.get('owner_serial_no'))

    # Build a status summary so the user sees what was matched vs missed
    fetched = []
    missed = []
    for label, val in [('brand', brand_v), ('model', model_v), ('year', year_v),
                       ('fuel', fuel_v), ('engine', engine_v), ('seats', seats_v),
                       ('owner count', owner_v)]:
        (fetched if val is not None else missed).append(label)

    raw_brand = data.get('maker_name', '?')
    raw_model = data.get('maker_model', '?')
    status = (f'✅ **{raw_brand} {raw_model}** ({year_v or "year?"} · {fuel_v or data.get("fuel_type", "?")})  \n'
              f'Auto-filled: {", ".join(fetched) if fetched else "—"}'
              + (f'  \n⚠️ Couldn\'t match: {", ".join(missed)} (please set manually)' if missed else ''))

    # Update model dropdown's choices to the brand's models (so user can pick if normalisation failed)
    model_update = (gr.update(choices=VOCAB['brand_to_models'].get(brand_v, []), value=model_v)
                    if brand_v else gr.update())

    return (
        gr.update(value=status),
        gr.update(value=brand_v) if brand_v else gr.update(),
        model_update,
        gr.update(value=year_v) if year_v else gr.update(),
        gr.update(value=fuel_v) if fuel_v else gr.update(),
        gr.update(value=int(engine_v)) if engine_v else gr.update(),
        gr.update(value=int(seats_v)) if seats_v else gr.update(),
        gr.update(value=owner_v) if owner_v else gr.update(),
    )


def validate_mobile(raw):
    if not raw or not raw.strip():
        return False, 'Mobile number is required.'
    cleaned = re.sub(r'[\s\-]', '', raw.strip())
    if not MOBILE_RE.match(cleaned):
        return False, 'Invalid mobile number — must be a 10-digit Indian mobile starting with 6/7/8/9.'
    return True, cleaned[-10:]


def models_for_brand(brand, current_model=None):
    """Update model dropdown choices for a brand. Preserve current_model if it's valid for the new brand."""
    if not brand:
        return gr.update(choices=[], value=None)
    models = VOCAB['brand_to_models'].get(brand, [])
    if current_model in models:
        return gr.update(choices=models, value=current_model)
    return gr.update(choices=models, value=models[0] if models else None)


def fill_defaults(brand, model):
    d = DEFAULTS_LOOKUP.get((brand, model))
    if not d:
        return gr.update(), gr.update(), gr.update(), gr.update()
    return (gr.update(value=int(d['engine'])), gr.update(value=float(d['max_power'])),
            gr.update(value=float(d['mileage'])), gr.update(value=int(d['seats'])))


def build_summary_md(adj_result):
    if adj_result.get('estimate') is None and adj_result.get('reason') == 'insufficient_data':
        return adj_result['message']

    lo, mid, hi = format_inr(adj_result['low']), format_inr(adj_result['mid']), format_inr(adj_result['high'])
    lines = [f"### {lo} – {hi}  *(est. {mid})*"]

    if adj_result.get('damage_detected'):
        cats_human = [CATEGORY_HUMAN.get(c, c) for c in adj_result['damage_categories']]
        spec = adj_result['spec_based_price']
        lines.append(
            f"\n⚠️ **Visible damage detected** — estimate reduced by **{adj_result['discount_pct']}%** from the spec-based price ({format_inr(spec['mid'])})."
        )
        lines.append(f"\nDetected: {', '.join(cats_human)}")
        lines.append(
            "\n*Note: Final price depends on damage severity, which requires physical inspection. This is an automated estimate only.*"
        )
    elif adj_result.get('damage_detected') is False:
        lines.append("\n✅ **No visible damage detected** in the uploaded photo — estimate stands.")
    return '\n'.join(lines)


def estimate(brand, model, reg_year, km_driven, fuel_type, transmission_type,
             seller_type, engine, max_power, mileage, seats,
             customer_mobile, owner_count, car_image):
    mobile_ok, mobile_result = validate_mobile(customer_mobile)
    if not mobile_ok:
        return f'⚠️ {mobile_result}', None, json.dumps({'error': mobile_result}, indent=2)

    vehicle_age = max(0, CURRENT_YEAR - int(reg_year))
    payload = {
        'brand': brand, 'model': model,
        'vehicle_age': vehicle_age, 'km_driven': int(km_driven),
        'fuel_type': fuel_type, 'transmission_type': transmission_type, 'seller_type': seller_type,
        'engine': int(engine), 'max_power': float(max_power),
        'mileage': float(mileage), 'seats': int(seats),
    }
    spec_result = predict(payload)

    annotated = None
    damage_info = {'image_provided': False}
    if car_image is not None:
        det = detect_damage(car_image)
        damage_info = {
            'image_provided': True,
            'image_ok': det['ok'],
            'message': det['message'],
            'categories_detected': det['categories_detected'],
            'raw_class_counts': det['raw_class_counts'],
        }
        if det['ok']:
            annotated = det['annotated_image']
            adj_result = apply_damage_discount(spec_result, det['categories_detected'])
        else:
            adj_result = spec_result
    else:
        adj_result = spec_result

    summary_md = build_summary_md(adj_result)
    if damage_info.get('image_provided') and not damage_info.get('image_ok'):
        summary_md += f"\n\nℹ️ Image issue: {damage_info['message']}"

    debug = {
        **adj_result,
        'lead_capture': {
            'customer_mobile': mobile_result,
            'owner_count': owner_count,
            'reg_year': int(reg_year),
            'derived_vehicle_age': vehicle_age,
        },
        'damage': damage_info,
    }
    return summary_md, annotated, json.dumps(debug, indent=2, default=str)


with gr.Blocks(title='VahanOne — AI Car Inspection') as demo:
    gr.Markdown(
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">'
        '<a href="/" style="text-decoration:none;color:#2563eb;font-size:14px">← Back to VahanOne</a>'
        '<span style="color:#888;font-size:13px">AI Car Inspection</span>'
        '</div>'
    )
    gr.Markdown('# Used Car Price Estimator')
    gr.Markdown(
        'Enter the vehicle registration number to auto-fill most details. '
        'Complete the remaining fields and (optionally) upload a photo for damage assessment.'
    )

    with gr.Row():
        rc_number = gr.Textbox(
            label='Vehicle registration number',
            placeholder='e.g. MH12DE1234',
            scale=3,
        )
        fetch_rc_btn = gr.Button('Fetch details from RC', variant='secondary', scale=1)
    rc_status = gr.Markdown(visible=True)

    with gr.Row():
        with gr.Column():
            brand = gr.Dropdown(choices=BRANDS, label='Brand', value=DEFAULT_BRAND)
            model = gr.Dropdown(choices=VOCAB['brand_to_models'][DEFAULT_BRAND], label='Model', value=DEFAULT_MODEL)
            reg_year = gr.Number(value=DEFAULT_REG_YEAR, precision=0,
                                 label=f'Year of registration (current year: {CURRENT_YEAR})')
            km_driven = gr.Number(value=50000, label='Kilometres driven', precision=0)

        with gr.Column():
            fuel_type = gr.Dropdown(choices=FUEL_TYPES, label='Fuel type', value='Petrol')
            transmission_type = gr.Dropdown(choices=TRANSMISSIONS, label='Transmission', value='Manual')
            seller_type = gr.Dropdown(choices=SELLER_TYPES, label='Seller type', value='Individual')
            owner_count = gr.Dropdown(choices=['1st owner', '2nd owner', '3rd owner', '4th+ owner'],
                                      value='1st owner', label='Number of previous owners')

        with gr.Column():
            engine = gr.Number(value=int(DEFAULT_SPECS['engine']), label='Engine (CC)', precision=0)
            max_power = gr.Number(value=float(DEFAULT_SPECS['max_power']), label='Max power (bhp) — optional')
            mileage = gr.Number(value=float(DEFAULT_SPECS['mileage']), label='Mileage (kmpl)')
            seats = gr.Number(value=int(DEFAULT_SPECS['seats']), label='Seats', precision=0)

    customer_mobile = gr.Textbox(label='Customer mobile number', placeholder='10-digit number')

    gr.Markdown('### Optional: upload a car photo for damage detection')
    car_image = gr.Image(label='Car photo', type='pil', height=320)

    submit_btn = gr.Button('Estimate price', variant='primary')

    with gr.Row():
        with gr.Column():
            output_summary = gr.Markdown(label='Price estimate')
        with gr.Column():
            output_annotated = gr.Image(label='Detected damage (annotated)', type='numpy', height=320)

    with gr.Accordion('Raw JSON (for debugging)', open=False):
        output_json = gr.Code(label='')

    fetch_rc_btn.click(
        fetch_rc_details,
        inputs=[rc_number],
        outputs=[rc_status, brand, model, reg_year, fuel_type, engine, seats, owner_count],
    )

    brand.change(models_for_brand, inputs=[brand, model], outputs=model)
    brand.change(fill_defaults, inputs=[brand, model], outputs=[engine, max_power, mileage, seats])
    model.change(fill_defaults, inputs=[brand, model], outputs=[engine, max_power, mileage, seats])

    submit_btn.click(
        estimate,
        inputs=[brand, model, reg_year, km_driven, fuel_type, transmission_type,
                seller_type, engine, max_power, mileage, seats,
                customer_mobile, owner_count, car_image],
        outputs=[output_summary, output_annotated, output_json],
    )

    gr.Markdown('---')
    gr.Markdown(
        'Estimate is automated and indicative only — actual price depends on physical inspection. '
        '<a href="/" style="text-decoration:none;color:#2563eb">Return to VahanOne</a>'
    )


if __name__ == '__main__':
    demo.launch(server_name='0.0.0.0', server_port=7860, root_path='/inspection')
