import math, csv, datetime, json
from pathlib import Path
import xml.etree.ElementTree as ET

_svg_ctr = 0  # ensures unique SVG element IDs within a single HTML page


# ── time ──────────────────────────────────────────────────────────────────────

def parse_time(ts: str):
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


# ── distance ──────────────────────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))


# ── parsers ───────────────────────────────────────────────────────────────────
# Point tuple: (lat, lon, ele, t, speed_mps, lean_deg)
# GPX:   speed_mps=None, lean_deg=None
# JSONL: ele=None

def parse_gpx_points(path: Path):
    pts = []
    root = ET.parse(str(path)).getroot()
    ns = {'gpx': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
    def f(el, q): return el.find(('gpx:' + q) if ns else q, ns)
    for trk in root.findall('.//gpx:trk' if ns else './/trk', ns):
        for seg in trk.findall('.//gpx:trkseg' if ns else './/trkseg', ns):
            for tp in seg.findall('.//gpx:trkpt' if ns else './/trkpt', ns):
                lat = float(tp.attrib['lat']); lon = float(tp.attrib['lon'])
                ele_el = f(tp, 'ele'); time_el = f(tp, 'time')
                ele = float(ele_el.text) if ele_el is not None else None
                t   = parse_time(time_el.text) if time_el is not None else None
                pts.append((lat, lon, ele, t, None, None))
    return pts


def parse_jsonl_points(path: Path):
    """Parses ride files exported by the MotorcycleTrackShare iOS app."""
    pts = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            lat = s.get('lat')
            lon = s.get('lon')
            if lat is None or lon is None:
                continue
            unix_t = s.get('t')
            t = datetime.datetime.fromtimestamp(unix_t, tz=datetime.timezone.utc) if unix_t is not None else None
            pts.append((lat, lon, None, t, s.get('speedMps'), s.get('leanDeg')))
    return pts


def load_points(path: Path):
    if path.suffix.lower() == '.gpx':
        return parse_gpx_points(path)
    if path.suffix.lower() == '.jsonl':
        return parse_jsonl_points(path)
    return []


# ── ride stats ────────────────────────────────────────────────────────────────

def ride_stats(points):
    empty = dict(distance_km=0.0, duration_h=0.0, elevation_gain_m=0.0,
                 max_elevation_m=None, avg_speed_kmh=0.0,
                 max_speed_kmh=None, max_lean_deg=None,
                 max_lean_right_deg=None, max_lean_left_deg=None)
    if len(points) < 2:
        return empty

    dist_km = elev_gain_m = 0.0
    max_ele = None
    t0 = t1 = None
    max_speed_mps = None
    lean_vals = []
    prev = None

    for (lat, lon, ele, t, speed_mps, lean_deg) in points:
        if prev:
            dist_km += haversine_km(prev[0], prev[1], lat, lon)
            if prev[2] is not None and ele is not None and ele > prev[2]:
                elev_gain_m += ele - prev[2]
        if ele is not None and (max_ele is None or ele > max_ele):
            max_ele = ele
        if t is not None:
            if t0 is None: t0 = t
            t1 = t
        if speed_mps is not None:
            if max_speed_mps is None or speed_mps > max_speed_mps:
                max_speed_mps = speed_mps
        if lean_deg is not None:
            lean_vals.append(lean_deg)
        prev = (lat, lon, ele, t, speed_mps, lean_deg)

    duration_h = (t1 - t0).total_seconds() / 3600.0 if (t0 and t1) else 0.0

    return dict(
        distance_km=dist_km,
        duration_h=duration_h,
        elevation_gain_m=elev_gain_m,
        max_elevation_m=max_ele,
        avg_speed_kmh=dist_km / duration_h if duration_h > 0 else 0.0,
        max_speed_kmh=max_speed_mps * 3.6 if max_speed_mps is not None else None,
        max_lean_deg=max((abs(v) for v in lean_vals), default=None),
        max_lean_right_deg=max((v for v in lean_vals if v > 0), default=None),
        max_lean_left_deg=max((abs(v) for v in lean_vals if v < 0), default=None),
    )


# ── acceleration analysis ─────────────────────────────────────────────────────

def _count_events(values, threshold):
    """Number of separate sustained runs where value exceeds threshold."""
    events, in_event = 0, False
    for v in values:
        if v > threshold:
            if not in_event:
                events += 1
                in_event = True
        else:
            in_event = False
    return events


def accel_series(points):
    """Returns (dist_km, accel_g). Filters GPS noise and large time gaps."""
    dist, accels = [], []
    cum = 0.0
    prev = None
    for pt in points:
        lat, lon, ele, t, speed_mps, lean_deg = pt
        if prev:
            cum += haversine_km(prev[0], prev[1], lat, lon)
            if (t is not None and prev[3] is not None and
                    speed_mps is not None and prev[4] is not None):
                dt = t - prev[3]
                dv = speed_mps - prev[4]
                if 0.04 <= dt <= 2.0:
                    a_g = (dv / dt) / 9.81
                    if abs(a_g) <= 2.5:   # cap obvious GPS noise
                        dist.append(cum)
                        accels.append(a_g)
        prev = pt
    return dist, accels


_STYLE_META = {
    'Smooth':     ('#3DB88A', 'rgba(61,184,138,0.12)',
                   'Calm, progressive inputs throughout. Consistent throttle and braking with low lean angles.'),
    'Spirited':   ('#FF9A3C', 'rgba(255,154,60,0.12)',
                   'Confident inputs with measured aggression. Good pace with controlled lean and purposeful braking.'),
    'Aggressive': ('#FF6D00', 'rgba(255,109,0,0.12)',
                   'Hard acceleration, strong braking, and committed lean angles. Pushing the machine.'),
    'Track Mode': ('#FF453A', 'rgba(255,69,58,0.12)',
                   'Maximum effort across all inputs. Extreme lean angles, hard launches, and late braking.'),
}


def riding_style_analysis(points, base_stats):
    """Returns acceleration metrics and a riding style classification."""
    d_accel, accel_g = accel_series(points)

    none_result = dict(max_accel_g=None, max_brake_g=None,
                       hard_accel_events=None, hard_brake_events=None,
                       smooth_pct=None, style=None, style_desc=None,
                       style_color=None, style_bg=None, aggression_score=None)

    if not accel_g:
        return none_result

    pos = [a for a in accel_g if a > 0]
    neg = [a for a in accel_g if a < 0]

    max_accel_g = max(pos, default=0.0)
    max_brake_g = abs(min(neg, default=0.0))
    hard_accel  = _count_events(accel_g,          0.25)
    hard_brake  = _count_events([-a for a in neg], 0.25)
    smooth_pct  = sum(1 for a in accel_g if abs(a) < 0.08) / len(accel_g) * 100

    max_lean = base_stats.get('max_lean_deg') or 0
    score = (
        min(40, max_lean    / 55  * 40) +
        min(30, max_accel_g / 0.5 * 30) +
        min(30, max_brake_g / 0.7 * 30)
    )

    if score < 20:
        style = 'Smooth'
    elif score < 45:
        style = 'Spirited'
    elif score < 70:
        style = 'Aggressive'
    else:
        style = 'Track Mode'

    fg, bg, desc = _STYLE_META[style]

    return dict(max_accel_g=max_accel_g, max_brake_g=max_brake_g,
                hard_accel_events=hard_accel, hard_brake_events=hard_brake,
                smooth_pct=smooth_pct, style=style, style_desc=desc,
                style_color=fg, style_bg=bg, aggression_score=score)


# ── chart series ──────────────────────────────────────────────────────────────

def _series(points, value_fn):
    dist, vals = [], []
    cum = 0.0
    prev_ll = None
    for pt in points:
        lat, lon = pt[0], pt[1]
        if prev_ll:
            cum += haversine_km(prev_ll[0], prev_ll[1], lat, lon)
        v = value_fn(pt)
        if v is not None:
            dist.append(cum)
            vals.append(v)
        prev_ll = (lat, lon)
    return dist, vals


def elevation_series(points): return _series(points, lambda p: p[2])
def speed_series(points):     return _series(points, lambda p: p[4] * 3.6 if p[4] is not None else None)
def lean_series(points):      return _series(points, lambda p: p[5])


# ── SVG builders ──────────────────────────────────────────────────────────────

def build_svg(dist, values, width=560, height=110, margin=32,
              color='#FF6D00', label='', zero_line=False):
    global _svg_ctr
    if not dist or len(dist) < 2:
        return ''

    _svg_ctr += 1
    sid = _svg_ctr
    W, H, M = width, height, margin
    pw, ph = W - 2*M, H - 2*M

    xmin, xmax = min(dist), max(dist)
    ymin, ymax = min(values), max(values)
    if zero_line:
        ya = max(abs(ymin), abs(ymax), 1.0)
        ymin, ymax = -ya, ya
    xr = (xmax - xmin) if xmax > xmin else 1.0
    yr = (ymax - ymin) if ymax > ymin else 1.0

    pts = ' '.join(
        f'{M + (x - xmin) * (pw / xr):.1f},{H - M - (v - ymin) * (ph / yr):.1f}'
        for x, v in zip(dist, values)
    )
    grid = ''.join(
        f'<line x1="{M}" y1="{M + i * ph / 2:.1f}" x2="{W-M}" y2="{M + i * ph / 2:.1f}" stroke="#383838" stroke-width="1"/>'
        for i in range(3)
    )
    zero_mark = ''
    if zero_line and ymin < 0 < ymax:
        zy = H - M - (0 - ymin) * (ph / yr)
        zero_mark = f'<line x1="{M}" y1="{zy:.1f}" x2="{W-M}" y2="{zy:.1f}" stroke="#595959" stroke-width="1" stroke-dasharray="4,3"/>'

    return (
        f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg"'
        f' role="img" aria-label="{label}" style="display:block;width:100%;height:auto;border-radius:10px">'
        f'<defs><linearGradient id="g{sid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.15"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<rect width="{W}" height="{H}" fill="#2F2F2F" rx="10"/>'
        f'{grid}'
        f'<line x1="{M}" y1="{H-M}" x2="{W-M}" y2="{H-M}" stroke="#383838" stroke-width="1"/>'
        f'<line x1="{M}" y1="{M}" x2="{M}" y2="{H-M}" stroke="#383838" stroke-width="1"/>'
        f'<text x="{M-5}" y="{M+4}" text-anchor="end" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">{ymax:.0f}</text>'
        f'<text x="{M-5}" y="{H-M+4}" text-anchor="end" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">{ymin:.0f}</text>'
        f'<text x="{M}" y="{H-4}" text-anchor="start" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">0 km</text>'
        f'<text x="{W-M}" y="{H-4}" text-anchor="end" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">{xmax:.1f} km</text>'
        f'{zero_mark}'
        f'<polygon fill="url(#g{sid})" points="{M},{H-M} {pts} {M+pw:.1f},{H-M}"/>'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>'
        f'</svg>'
    )


def build_accel_svg(dist, accel_g, width=560, height=110, margin=32):
    """Dual-color SVG: ember above zero (acceleration), blue below (braking)."""
    global _svg_ctr
    if not dist or len(dist) < 2:
        return ''

    _svg_ctr += 1
    sid = _svg_ctr
    W, H, M = width, height, margin
    pw, ph = W - 2*M, H - 2*M

    xmin, xmax = min(dist), max(dist)
    ya   = max(max(abs(v) for v in accel_g), 0.1)
    ymin, ymax = -ya, ya
    xr   = (xmax - xmin) if xmax > xmin else 1.0
    yr   = ymax - ymin

    zy = H - M - (0 - ymin) * (ph / yr)    # pixel y of the zero line

    pts = ' '.join(
        f'{M + (x - xmin) * (pw / xr):.1f},{H - M - (v - ymin) * (ph / yr):.1f}'
        for x, v in zip(dist, accel_g)
    )
    first_x = f'{M:.1f}'
    last_x  = f'{M + pw:.1f}'
    poly    = f'{first_x},{zy:.1f} {pts} {last_x},{zy:.1f}'

    clip_pos_h = max(0.0, zy - M)
    clip_neg_h = max(0.0, H - M - zy)

    grid = ''.join(
        f'<line x1="{M}" y1="{M + i * ph / 2:.1f}" x2="{W-M}" y2="{M + i * ph / 2:.1f}" stroke="#383838" stroke-width="1"/>'
        for i in range(3)
    )

    return (
        f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg"'
        f' role="img" aria-label="Acceleration (g)" style="display:block;width:100%;height:auto;border-radius:10px">'
        f'<defs>'
        f'<linearGradient id="ag{sid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#FF6D00" stop-opacity="0.35"/>'
        f'<stop offset="100%" stop-color="#FF6D00" stop-opacity="0"/></linearGradient>'
        f'<linearGradient id="bg{sid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#4D9EFF" stop-opacity="0"/>'
        f'<stop offset="100%" stop-color="#4D9EFF" stop-opacity="0.35"/></linearGradient>'
        f'<clipPath id="cp{sid}"><rect x="{M}" y="{M}" width="{pw}" height="{clip_pos_h:.1f}"/></clipPath>'
        f'<clipPath id="cn{sid}"><rect x="{M}" y="{zy:.1f}" width="{pw}" height="{clip_neg_h:.1f}"/></clipPath>'
        f'</defs>'
        f'<rect width="{W}" height="{H}" fill="#2F2F2F" rx="10"/>'
        f'{grid}'
        f'<line x1="{M}" y1="{H-M}" x2="{W-M}" y2="{H-M}" stroke="#383838" stroke-width="1"/>'
        f'<line x1="{M}" y1="{M}" x2="{M}" y2="{H-M}" stroke="#383838" stroke-width="1"/>'
        # filled areas
        f'<polygon fill="url(#ag{sid})" clip-path="url(#cp{sid})" points="{poly}"/>'
        f'<polygon fill="url(#bg{sid})" clip-path="url(#cn{sid})" points="{poly}"/>'
        # zero line
        f'<line x1="{M}" y1="{zy:.1f}" x2="{W-M}" y2="{zy:.1f}" stroke="#595959" stroke-width="1" stroke-dasharray="4,3"/>'
        # axis labels
        f'<text x="{M-5}" y="{M+4}" text-anchor="end" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">+{ya:.1f}g</text>'
        f'<text x="{M-5}" y="{H-M+4}" text-anchor="end" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">-{ya:.1f}g</text>'
        f'<text x="{M}" y="{H-4}" text-anchor="start" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">0 km</text>'
        f'<text x="{W-M}" y="{H-4}" text-anchor="end" fill="#595959" font-size="9" font-family="-apple-system,system-ui,sans-serif">{xmax:.1f} km</text>'
        # data line in off-white so it reads over both fill colors
        f'<polyline fill="none" stroke="#C8C8C6" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>'
        f'</svg>'
    )


# ── HTML helpers ──────────────────────────────────────────────────────────────

_CSS = """\
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --void:           #181818;
  --surface:        #242424;
  --surface-raised: #2F2F2F;
  --divider:        #383838;
  --text-primary:   #F0F0EE;
  --text-secondary: #8C8C8C;
  --text-tertiary:  #737373;
  --text-ghost:     #595959;
  --ember:          #FF6D00;
  --radius:         16px;
  --radius-sm:      10px;
}

body {
  background: var(--void);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
  padding: 44px 24px 72px;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}

.page-header { margin-bottom: 32px; max-width: 840px; margin-left: auto; margin-right: auto; }
.page-title { font-size: 34px; font-weight: 700; letter-spacing: -0.5px; line-height: 1.1; margin-bottom: 6px; }
.page-meta { font-size: 15px; color: var(--text-secondary); }

.rides { display: flex; flex-direction: column; gap: 16px; max-width: 840px; margin: 0 auto; }

.empty { padding: 56px 24px; text-align: center; }
.empty-title { font-size: 17px; font-weight: 600; margin-bottom: 8px; }
.empty-body { font-size: 15px; color: var(--text-secondary); }

.ride-card { background: var(--surface); border-radius: var(--radius); overflow: hidden; }

.ride-card-header {
  background: var(--ember);
  padding: 12px 16px;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  min-height: 48px;
}
.ride-name { font-size: 17px; font-weight: 600; color: #F0F0EE; letter-spacing: -0.2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.source-badge { flex-shrink: 0; font-size: 10px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; padding: 4px 8px; border-radius: 6px; background: rgba(0,0,0,0.2); color: rgba(255,255,255,0.82); }

.ride-card-body { padding: 16px; }

.stats-primary { display: grid; grid-template-columns: repeat(3, 1fr); padding-bottom: 14px; }
.stats-highlight { display: grid; grid-template-columns: repeat(2, 1fr); padding: 14px 0; border-top: 1px solid var(--divider); border-bottom: 1px solid var(--divider); margin-bottom: 16px; }

.stat { display: flex; flex-direction: column; gap: 3px; }
.stat-value { font-size: 20px; font-weight: 600; letter-spacing: -0.3px; line-height: 1.15; color: var(--text-primary); }
.stat-value.ember { color: var(--ember); }
.stat-value.na    { color: var(--text-ghost); font-weight: 400; font-size: 18px; }
.stat-sub  { font-size: 11px; color: var(--text-tertiary); line-height: 1.3; }
.stat-label { font-size: 11px; font-weight: 500; letter-spacing: 0.4px; text-transform: uppercase; color: var(--text-secondary); }

/* Riding style section */
.style-section {
  padding: 14px 0 16px;
  border-top: 1px solid var(--divider);
  border-bottom: 1px solid var(--divider);
  margin-bottom: 16px;
  display: flex; flex-direction: column; gap: 10px;
}
.style-header { display: flex; align-items: center; justify-content: space-between; }
.style-badge { font-size: 10px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; padding: 4px 10px; border-radius: 20px; }
.style-desc { font-size: 13px; color: var(--text-secondary); line-height: 1.5; }
.accel-stats { display: grid; grid-template-columns: repeat(3, 1fr); }

.charts { display: flex; flex-direction: column; gap: 16px; }
.chart-block { display: flex; flex-direction: column; gap: 7px; }
.chart-heading { font-size: 11px; font-weight: 500; letter-spacing: 0.4px; text-transform: uppercase; color: var(--text-secondary); }

.accel-legend { display: flex; gap: 16px; margin-top: 5px; }
.legend-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--text-secondary); }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

@media (max-width: 480px) {
  body { padding: 28px 16px 56px; }
  .page-title { font-size: 28px; }
  .stat-value { font-size: 17px; }
  .stat-value.na { font-size: 16px; }
}
"""


def _stat(label, display, cls='', sub=''):
    vc = f'stat-value {cls}'.strip()
    sub_html = f'<span class="stat-sub">{sub}</span>' if sub else ''
    return (f'<div class="stat">'
            f'<span class="{vc}">{display}</span>'
            f'{sub_html}'
            f'<span class="stat-label">{label}</span>'
            f'</div>')


def _fmt(v, dec=1):
    return f'{float(v):.{dec}f}' if v is not None else None


def _style_section_html(sd):
    if not sd or sd.get('style') is None:
        return ''

    fg   = sd['style_color']
    bg   = sd['style_bg']
    desc = sd['style_desc']

    def fg_val(v, suf=''):
        return f'{v:.2f}{suf}' if v is not None else '—'
    def ev(v):
        return f'{v}' if v is not None else '—'
    def pct(v):
        return f'{v:.0f}%' if v is not None else '—'

    return (
        f'<div class="style-section">'
        f'<div class="style-header">'
        f'<span class="chart-heading">Riding Style</span>'
        f'<span class="style-badge" style="background:{bg};color:{fg}">{sd["style"]}</span>'
        f'</div>'
        f'<p class="style-desc">{desc}</p>'
        f'<div class="accel-stats">'
        + _stat('Peak Accel',  fg_val(sd["max_accel_g"], 'g'), sub=f'{ev(sd["hard_accel_events"])} hard events')
        + _stat('Peak Brake',  fg_val(sd["max_brake_g"], 'g'), sub=f'{ev(sd["hard_brake_events"])} hard events')
        + _stat('Smooth',      pct(sd["smooth_pct"]),          sub='of ride time')
        + '</div></div>'
    )


def _ride_card(r, charts, style_data):
    source = 'iOS App' if r['file'].lower().endswith('.jsonl') else 'GPX'

    spd_disp  = f'{_fmt(r["max_speed_kmh"])} km/h' if r['max_speed_kmh'] is not None else '—'
    spd_cls   = 'ember' if r['max_speed_kmh'] is not None else 'na'
    lean_disp = f'{_fmt(r["max_lean_deg"])}°' if r['max_lean_deg'] is not None else '—'
    lean_cls  = 'ember' if r['max_lean_deg'] is not None else 'na'

    accel_legend = (
        '<div class="accel-legend">'
        '<span class="legend-item"><span class="legend-dot" style="background:#FF6D00"></span>Acceleration</span>'
        '<span class="legend-item"><span class="legend-dot" style="background:#4D9EFF"></span>Braking</span>'
        '</div>'
    ) if charts.get('accel') else ''

    chart_defs = [
        ('Speed (km/h)',         'speed'),
        ('Lean Angle (°)',       'lean'),
        ('Acceleration (g)',     'accel'),
        ('Elevation (m)',        'elevation'),
    ]
    charts_html = '<div class="charts">' + ''.join(
        f'<div class="chart-block">'
        f'<span class="chart-heading">{h}</span>'
        f'{charts[k]}'
        f'{"" if k != "accel" else accel_legend}'
        f'</div>'
        for h, k in chart_defs if charts.get(k)
    ) + '</div>'

    return (
        '<div class="ride-card">'
        f'<div class="ride-card-header">'
        f'<span class="ride-name">{r["file"]}</span>'
        f'<span class="source-badge">{source}</span>'
        f'</div>'
        '<div class="ride-card-body">'
        '<div class="stats-primary">'
        + _stat('Distance',  f'{_fmt(r["distance_km"], 2)} km')
        + _stat('Duration',  f'{_fmt(r["duration_h"],  2)} h')
        + _stat('Avg Speed', f'{_fmt(r["avg_speed_kmh"])} km/h')
        + '</div>'
        '<div class="stats-highlight">'
        + _stat('Max Speed', spd_disp, spd_cls)
        + _stat('Max Lean',  lean_disp, lean_cls)
        + '</div>'
        + _style_section_html(style_data)
        + charts_html
        + '</div></div>'
    )


def build_html(rows, charts_by_file, style_by_file):
    count = len(rows)
    meta  = f'{count} ride{"s" if count != 1 else ""} analyzed'

    if not rows:
        body = ('<div class="empty">'
                '<div class="empty-title">No rides found</div>'
                '<p class="empty-body">Drop .gpx or .jsonl files into this folder and run main.py.</p>'
                '</div>')
    else:
        body = '<div class="rides">' + ''.join(
            _ride_card(r, charts_by_file.get(r['file'], {}), style_by_file.get(r['file']))
            for r in rows
        ) + '</div>'

    return '\n'.join([
        '<!doctype html>', '<html lang="en">', '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>Ride Report</title>',
        f'<style>{_CSS}</style>',
        '</head>', '<body>',
        '<div class="page-header">',
        '<h1 class="page-title">Ride Report</h1>',
        f'<p class="page-meta">{meta}</p>',
        '</div>', body, '</body>', '</html>',
    ])


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    global _svg_ctr
    _svg_ctr = 0

    here = Path(__file__).parent
    all_files = (
        sorted([*here.glob('*.gpx'), *here.glob('*.GPX')]) +
        sorted([*here.glob('*.jsonl'), *here.glob('*.JSONL')])
    )

    rows, charts_by_file, style_by_file = [], {}, {}

    for fp in all_files:
        pts   = load_points(fp)
        stats = ride_stats(pts)
        sd    = riding_style_analysis(pts, stats)

        d_el, el  = elevation_series(pts)
        d_sp, sp  = speed_series(pts)
        d_ln, ln  = lean_series(pts)
        d_ac, ac  = accel_series(pts)

        charts_by_file[fp.name] = {
            'elevation': build_svg(d_el, el, color='#3DB88A', label='Elevation'),
            'speed':     build_svg(d_sp, sp, color='#FF6D00', label='Speed'),
            'lean':      build_svg(d_ln, ln, color='#FF9A3C', label='Lean', zero_line=True),
            'accel':     build_accel_svg(d_ac, ac),
        }
        style_by_file[fp.name] = sd
        rows.append({'file': fp.name, **stats})

    # CSV
    csv_fields = [
        'file', 'distance_km', 'duration_h', 'avg_speed_kmh', 'max_speed_kmh',
        'elevation_gain_m', 'max_elevation_m',
        'max_lean_deg', 'max_lean_right_deg', 'max_lean_left_deg',
        'max_accel_g', 'max_brake_g', 'hard_accel_events', 'hard_brake_events',
        'smooth_pct', 'riding_style',
    ]
    csv_path = here / 'gpx_summary.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(csv_fields)
        for r in rows:
            sd = style_by_file.get(r['file'], {}) or {}
            def fmt(v): return f'{float(v):.6f}' if v is not None else ''
            w.writerow([
                r['file'],
                fmt(r['distance_km']), fmt(r['duration_h']),
                fmt(r['avg_speed_kmh']), fmt(r['max_speed_kmh']),
                fmt(r['elevation_gain_m']), fmt(r['max_elevation_m']),
                fmt(r['max_lean_deg']), fmt(r['max_lean_right_deg']), fmt(r['max_lean_left_deg']),
                fmt(sd.get('max_accel_g')), fmt(sd.get('max_brake_g')),
                sd.get('hard_accel_events', ''), sd.get('hard_brake_events', ''),
                fmt(sd.get('smooth_pct')), sd.get('style', ''),
            ])

    # HTML
    html_path = here / 'gpx_report.html'
    html_path.write_text(build_html(rows, charts_by_file, style_by_file), encoding='utf-8')

    print(f"Wrote {csv_path.name} and {html_path.name} ({len(rows)} ride{'s' if len(rows) != 1 else ''})")


if __name__ == '__main__':
    main()
