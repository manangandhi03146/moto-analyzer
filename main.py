import gpxpy
import gpxpy.gpx
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime
import gpxpy.geo
import pandas as pd

here = Path(__file__).parent
in_dir = (here / "rides") if (here / "rides").exists() else here
files = sorted([*in_dir.glob("*.gpx"), *in_dir.glob("*.GPX")])

if not files:
    print("No .gpx files found. Put them in ./rides or next to main.py")

Point = Tuple[float, float, Optional[float], Optional[datetime]]

def load_points(path: Path) -> List[Point]:
    with path.open('r', encoding='utf-8') as gpx_file:
        gpx = gpxpy.parse(gpx_file)

    pts: List[Point] = []
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                pts.append((p.latitude, p.longitude, p.elevation, p.time))
    return pts

def compute_distance_km(points: List[Point]) -> float:
    if len(points) < 2:
        return 0.0
    
    total_distance = 0.0

    for i in range(1, len(points)):
        prev = points[i - 1]
        curr = points[i]
        d = gpxpy.geo.haversine_distance(prev[0], prev[1], curr[0], curr[1])
        total_distance += d

    return total_distance / 1000.0
    
def compute_duration_h(points: List[Point]) -> float:
    t_first = next((p[3] for p in points if p[3] is not None), None)
    t_last = next((p[3] for p in reversed(points) if p[3] is not None), None)

    if t_first is None or t_last is None:
        return 0.0 
    
    return (t_last - t_first).total_seconds() / 3600.0

def compute_elev_stats(points: List[Point]) -> tuple[float, float, float, Optional[float], Optional[float]]:
    gain = 0.0
    loss = 0.0
    max_ele: Optional[float] = None
    min_ele: Optional[float] = None

    prev_ele: Optional[float] = None
    first_ele: Optional[float] = None
    last_ele: Optional[float] = None

    for _, _, ele, _ in points:
        if ele is None:
            continue

        if first_ele is None:
            first_ele = ele

        last_ele = ele

        if (max_ele is None) or (ele > max_ele):
            max_ele = ele

        if (min_ele is None) or (ele < min_ele):
            min_ele = ele

        if prev_ele is not None:
            delta = ele - prev_ele
            if delta > 0:
                gain += delta
            elif delta < 0:
                loss += -delta

        prev_ele = ele

    net = 0.0
    if (first_ele is not None) and (last_ele is not None):
        net = last_ele - first_ele

    return gain, loss, net, max_ele, min_ele

def elevation_series(points: List[Point]) -> Tuple[list[float], list[float]]:
    dist_km_list: list[float] = []
    elev_m_list: list[float] = []

    cum_m = 0.0
    prev: Optional[Point] = None

    for pt in points:
        if prev is not None:
            d_m = gpxpy.geo.haversine_distance(prev[0], prev[1], pt[0], pt[1])
            cum_m += d_m

        if pt[2] is not None:
            dist_km_list.append(cum_m / 1000.0)
            elev_m_list.append(pt[2])

        prev = pt

    return dist_km_list, elev_m_list

def build_elevation_svg(dist, elev, width=520, height=140, margin=24) -> str:
    if not dist or not elev or len(dist) != len(elev):
        return ""

    min_elev = min(elev)
    max_elev = max(elev)
    elev_range = max_elev - min_elev if max_elev > min_elev else 1.0

    min_dist = min(dist)
    max_dist = max(dist)
    dist_range = max_dist - min_dist if max_dist > min_dist else 1.0

    def scale_x(x):
        return margin + (x - min_dist) / dist_range * (width - 2 * margin)

    def scale_y(y):
        return height - (margin + (y - min_elev) / elev_range * (height - 2 * margin))

    points_str = " ".join(f"{scale_x(d):.1f},{scale_y(e):.1f}" for d, e in zip(dist, elev))

    svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" version="1.1">
    <polyline points="{points_str}" fill="none" stroke="black" stroke-width="1"/>
    <rect x="{margin}" y="{margin}" width="{width - 2 * margin}" height="{height - 2 * margin}" fill="none" stroke="lightgray" stroke-width="1"/>
    </svg>'''
    return svg

def write_csv(rows: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "rides_summary.csv"
    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    return out_path

def write_html(rows: list[dict], svgs: dict[str, str], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "rides_summary.html"

    cols = [
        "name","points","distance_km","duration_h","avg_speed_kmh",
        "elevation_gain_m","elevation_loss_m","net_elevation_m",
        "max_elevation_m","min_elevation_m"
    ]

    html_rows = []
    for row in rows:
        name = row.get("name", "")
        cells = "".join(f"<td>{row.get(c, '')}</td>" for c in cols)
        svg = svgs.get(name, "")
        html_rows.append(f"<tr>{cells}<td>{svg}</td></tr>")

    html_content = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Rides Summary</title>
<style>
body{{font-family:Arial;margin:16px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:8px;text-align:left}}
th{{background:#f7f7f7}}
svg{{max-width:520px}}
</style></head><body>
<h1>Rides Summary</h1>
<table><thead><tr>
{''.join(f'<th>{c.replace("_"," ").title()}</th>' for c in cols)}
<th>Elevation Profile</th>
</tr></thead><tbody>
{''.join(html_rows)}
</tbody></table>
</body></html>"""

    out_path.write_text(html_content, encoding="utf-8")
    return out_path  

def main():
    out_dir = here / "out"
    rows: list[dict] = []
    svgs: dict[str, str] = {}

    for fp in files:
        pts = load_points(fp)

        dist_km = compute_distance_km(pts)
        dur_h   = compute_duration_h(pts)
        gain_m, loss_m, net_m, max_ele, min_ele = compute_elev_stats(pts)
        avg_kmh = dist_km / dur_h if dur_h > 0.0 else 0.0

        dist_series, elev_series = elevation_series(pts)
        svg = build_elevation_svg(dist_series, elev_series)

        svgs[fp.name] = svg

        rows.append({
            "name": fp.name,
            "points": len(pts),
            "distance_km": f"{dist_km:.6f}",
            "duration_h": f"{dur_h:.6f}",
            "avg_speed_kmh": f"{avg_kmh:.6f}",
            "elevation_gain_m": f"{gain_m:.1f}",
            "elevation_loss_m": f"{loss_m:.1f}",
            "net_elevation_m": f"{net_m:.1f}",
            "max_elevation_m": "" if max_ele is None else f"{max_ele:.1f}",
            "min_elevation_m": "" if min_ele is None else f"{min_ele:.1f}",
        })

        print(
            f"{fp.name:30} pts={len(pts)} dist={dist_km:.2f} km dur={dur_h:.2f} h "
            f"avg={avg_kmh:.2f} km/h gain={gain_m:.0f} m loss={loss_m:.0f} m net={net_m:+.0f} m"
        )

    csv_path  = write_csv(rows, out_dir)
    html_path = write_html(rows, svgs, out_dir)
    print(f"Wrote {csv_path}")
    print(f"Wrote {html_path}")

if __name__ == "__main__":
    main()
