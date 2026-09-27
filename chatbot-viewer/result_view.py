"""Render interactive Panel cards as an inline HTML document inside the Dash UI."""
from html import escape
from io import BytesIO, StringIO

from bokeh.resources import INLINE
import panel as pn
from PIL import Image

from RAG_delivery.object_search import capture_time, local_crop

pn.extension()


def text(value):
    return escape(str(value or "Not recorded"))


def render_results(payload, config):
    items = []
    if payload.get("message"):
        items.append(pn.pane.HTML(f'<p class="notice">{text(payload["message"])}</p>'))
    for group in payload.get("groups", []):
        title = text(group["query_object"]).capitalize()
        found, shown = group["total_instances"], len(group["results"])
        items.append(pn.pane.HTML(f'<h2>{title}</h2><p class="meta">{shown} of {found} matching instances · latest sightings first</p>'))
        if not found:
            items.append(pn.pane.HTML('<p class="notice">No matching sightings at this tolerance. Try a simpler object name or a broader match.</p>'))
        for record in group["results"]:
            image_pane = pn.pane.HTML('<p class="meta">Crop image is not available locally.</p>', width=220)
            path = local_crop(record, config)
            if path:
                try:
                    with Image.open(path) as original:
                        image = original.convert("RGB")
                        image.thumbnail((600, 450))
                        buffer = BytesIO()
                        image.save(buffer, format="PNG")
                    image_pane = pn.pane.PNG(buffer.getvalue(), width=220, max_height=240, sizing_mode="scale_width")
                except (OSError, ValueError):
                    pass
            seen = capture_time(record.get("timestamp"))
            timestamp = seen.strftime("%d %b %Y, %H:%M:%S UTC") if seen else "Capture time unavailable"
            scene = record.get("scene_meta")
            if not isinstance(scene, dict):
                scene = {}
            content = f'''<div class="context">
<div class="seen">LAST SEEN · {text(timestamp)}</div>
<h3>{text(record.get("object_name"))}</h3>
<p>{text(record.get("object_description"))}</p>
<dl><dt>Location</dt><dd>{text(record.get("object_location"))}</dd>
<dt>Environment</dt><dd>{text(scene.get("environment"))}</dd>
<dt>Scene</dt><dd>{text(scene.get("description"))}</dd>
<dt>Lighting</dt><dd>{text(scene.get("lighting"))}</dd></dl>
<p class="meta">Captured by {text(record.get("username") or "Unknown user")}</p>
</div>'''
            details = pn.pane.HTML(f'''<details><summary>Sighting details</summary>
<p>Source: {text(record.get("parent_image"))}</p>
<p>Object group: {text(record.get("object_id") or record["record_id"])}</p>
<p>Record: {text(record["record_id"])}</p>
<p>Closest stored label: {text(record["matched_label"])} · cosine distance {record["match_distance"]:.3f}</p>
</details>''', sizing_mode="stretch_width")
            # FlexBox lets the image and context stack on narrow displays.
            row = pn.FlexBox(image_pane, pn.pane.HTML(content, min_width=260, sizing_mode="stretch_width"),
                             flex_wrap="wrap", sizing_mode="stretch_width")
            items.append(pn.Card(row, details, title=text(record.get("object_name")),
                                 collapsed=False, sizing_mode="stretch_width", margin=(0, 0, 16, 0)))
    if payload.get("invalid_records"):
        items.append(pn.pane.HTML(f'<p class="notice">{payload["invalid_records"]} records had invalid timestamps or vectors and could not be fully searched.</p>'))
    css = '''<style>
body{background:#f5f7f3;color:#26392f;font-family:system-ui,sans-serif;margin:0}
h2{font-size:23px;margin:20px 0 6px} h3{font-size:22px;margin:8px 0}
p{line-height:1.6} .meta{font-size:13px;color:#64736a}.seen{color:#286747;font-size:11px;letter-spacing:.07em;font-weight:700}
dl{display:grid;grid-template-columns:90px 1fr;gap:8px;font-size:14px;line-height:1.5}dt{font-weight:600}dd{margin:0}
details{font-size:12px;color:#64736a;overflow-wrap:anywhere}summary{cursor:pointer}.notice{padding:18px;background:#e8eee6;border-radius:12px}
.context{padding:0 12px}
</style>'''
    panel = pn.Column(pn.pane.HTML(css, height=0, margin=0), *items, sizing_mode="stretch_width", margin=12)
    output = StringIO()
    # INLINE keeps Panel's JavaScript/CSS self-contained; no second server or CDN.
    panel.save(output, resources=INLINE, title="Latest object sightings")
    return output.getvalue()
