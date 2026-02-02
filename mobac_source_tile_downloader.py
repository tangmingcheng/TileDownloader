from __future__ import annotations

import argparse
import math
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
from PIL import Image

import httpx


# -------------------------
# WebMercator XYZ tile math
# -------------------------
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def lonlat_to_xyz_tile(lon: float, lat: float, z: int) -> Tuple[int, int]:
    lat = _clamp(lat, -85.05112878, 85.05112878)
    lon = ((lon + 180.0) % 360.0) - 180.0

    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n

    xi = int(_clamp(math.floor(x), 0, n - 1))
    yi = int(_clamp(math.floor(y), 0, n - 1))
    return xi, yi

def bbox_to_xyz_range(min_lon: float, min_lat: float, max_lon: float, max_lat: float, z: int) -> Tuple[int, int, int, int]:
    if max_lon < min_lon:
        min_lon, max_lon = max_lon, min_lon
    if max_lat < min_lat:
        min_lat, max_lat = max_lat, min_lat

    # top-left, bottom-right
    x0, y0 = lonlat_to_xyz_tile(min_lon, max_lat, z)
    x1, y1 = lonlat_to_xyz_tile(max_lon, min_lat, z)

    x_min, x_max = sorted((x0, x1))
    y_min, y_max = sorted((y0, y1))
    return x_min, x_max, y_min, y_max

def composite_tiles_for_layers(
    layer_dirs: List[Path],   # 每层的输出根目录（里面是 z/x/y.ext）
    zooms: List[int],
    out_dir: Path
) -> None:
    """
    将多个 layer 的瓦片按顺序 alpha 叠加，输出 png 到 out_dir/{z}/{x}/{y}.png
    layer_dirs 顺序很重要：第一个为底图，后面依次叠加
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    for z in zooms:
        # 以第一层的 z 目录作为遍历基础
        zdir = layer_dirs[0] / str(z)
        if not zdir.exists():
            print(f"[composite] skip z={z}, base layer missing")
            continue

        # 遍历 x 目录
        for xdir in zdir.iterdir():
            if not xdir.is_dir():
                continue
            x = xdir.name
            # 遍历 y 文件
            for yfile in xdir.iterdir():
                if not yfile.is_file():
                    continue
                y_stem = yfile.stem  # y
                # 输出路径（统一 png）
                out_path = out_dir / str(z) / x / f"{y_stem}.png"
                if out_path.exists():
                    continue

                # 读底图
                try:
                    base = Image.open(yfile).convert("RGBA")
                except Exception:
                    continue

                # 叠加其它层
                for layer_i in range(1, len(layer_dirs)):
                    # overlay 可能是 png/jpg/webp，找同名任意后缀
                    overlay_dir = layer_dirs[layer_i] / str(z) / x
                    if not overlay_dir.exists():
                        continue

                    # 同 y 的文件可能是 y.png/y.jpg/y.webp
                    cand = None
                    for ext in (".png", ".jpg", ".jpeg", ".webp"):
                        p = overlay_dir / f"{y_stem}{ext}"
                        if p.exists():
                            cand = p
                            break
                    if not cand:
                        continue

                    try:
                        overlay = Image.open(cand).convert("RGBA")
                        # alpha composite 要求同尺寸；不同尺寸直接跳过（不常见）
                        if overlay.size != base.size:
                            continue
                        base = Image.alpha_composite(base, overlay)
                    except Exception:
                        continue

                out_path.parent.mkdir(parents=True, exist_ok=True)
                base.save(out_path, format="PNG")

        print(f"[composite] z={z} done")


# -------------------------
# MOBAC XML parsing
# -------------------------
@dataclass
class LayerSource:
    name: str
    url_tpl: str              # contains {$x}{$y}{$z}, and maybe {$serverpart}
    tile_type: str            # png/jpg/...
    min_zoom: int
    max_zoom: int
    server_parts: List[str]   # e.g. ["1","2","3","4"] ; empty means no serverpart

@dataclass
class MapSource:
    name: str
    layers: List[LayerSource]  # single-layer -> len=1

def _text(node: Optional[ET.Element]) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()

def _extract_url(raw: str) -> str:
    # keep it as-is, just strip surrounding whitespace/newlines
    return "\n".join([line.strip() for line in raw.splitlines() if line.strip()])

def parse_mobac_xml(path: Path) -> Optional[MapSource]:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None

    tag = root.tag.lower()
    # support <customMapSource> and <customMultiLayerMapSource>
    if tag.endswith("custommapsource"):
        name = _text(root.find("name")) or path.stem
        url = _extract_url(_text(root.find("url")))
        tile_type = (_text(root.find("tileType")) or "png").lower()
        min_zoom = int(_text(root.find("minZoom")) or "0")
        max_zoom = int(_text(root.find("maxZoom")) or "18")
        server_parts = _text(root.find("serverParts")).split()
        layer = LayerSource(name=name, url_tpl=url, tile_type=tile_type,
                            min_zoom=min_zoom, max_zoom=max_zoom,
                            server_parts=server_parts)
        return MapSource(name=name, layers=[layer])

    if tag.endswith("custommultilayermapsource"):
        name = _text(root.find("name")) or path.stem
        layers_node = root.find("layers")
        if layers_node is None:
            return None

        layers: List[LayerSource] = []
        for cs in layers_node.findall("customMapSource"):
            lname = _text(cs.find("name")) or "layer"
            url = _extract_url(_text(cs.find("url")))
            tile_type = (_text(cs.find("tileType")) or "png").lower()
            min_zoom = int(_text(cs.find("minZoom")) or "0")
            max_zoom = int(_text(cs.find("maxZoom")) or "18")
            server_parts = _text(cs.find("serverParts")).split()
            layers.append(LayerSource(
                name=lname, url_tpl=url, tile_type=tile_type,
                min_zoom=min_zoom, max_zoom=max_zoom,
                server_parts=server_parts
            ))
        if not layers:
            return None
        return MapSource(name=name, layers=layers)

    return None

def load_sources_from_dir(src_dir: Path) -> List[Tuple[Path, MapSource]]:
    out: List[Tuple[Path, MapSource]] = []
    for p in sorted(src_dir.glob("*.xml")):
        ms = parse_mobac_xml(p)
        if ms:
            out.append((p, ms))
    return out


# -------------------------
# URL rendering (MOBAC style)
# -------------------------
_SERVERPART_RE = re.compile(r"\{\$serverpart\}", re.IGNORECASE)

def render_mobac_url(layer: LayerSource, z: int, x: int, y: int) -> str:
    url = layer.url_tpl
    url = url.replace("{$x}", str(x)).replace("{$y}", str(y)).replace("{$z}", str(z))

    if _SERVERPART_RE.search(url):
        if layer.server_parts:
            sp = random.choice(layer.server_parts)
        else:
            # if no parts provided, fall back to empty
            sp = ""
        url = _SERVERPART_RE.sub(sp, url)
    return url


# -------------------------
# Download
# -------------------------
def parse_zooms(s: str) -> List[int]:
    """
    "12" / "10-16" / "10-12,15,18-19"
    """
    s = (s or "").strip()
    if not s:
        return []
    out: List[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a.strip()), int(b.strip())
            if b < a:
                a, b = b, a
            out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return sorted(set(out))

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def download_range_for_layer(
    layer: LayerSource,
    zooms: List[int],
    out_dir: Path,
    *,
    bbox: Optional[Tuple[float, float, float, float]],
    xrange_: Optional[Tuple[int, int]],
    yrange_: Optional[Tuple[int, int]],
    world: bool,
    concurrency: int,
    timeout_s: float,
    retries: int,
    backoff_s: float,
    qps: float,
    headers: Dict[str, str],
) -> None:
    if not world and bbox is None and (xrange_ is None or yrange_ is None):
        raise SystemExit("必须提供 --bbox 或 --xrange/--yrange；若要全世界请显式加 --world")

    # build per zoom ranges
    jobs: List[Tuple[int,int,int]] = []  # (z,x,y)
    for z in zooms:
        if z < layer.min_zoom or z > layer.max_zoom:
            print(f"[skip] zoom {z} out of layer range [{layer.min_zoom},{layer.max_zoom}] for layer={layer.name}")
            continue

        n = 2 ** z
        if world:
            x_min, x_max, y_min, y_max = 0, n - 1, 0, n - 1
        elif bbox is not None:
            x_min, x_max, y_min, y_max = bbox_to_xyz_range(*bbox, z)
        else:
            x_min, x_max = xrange_
            y_min, y_max = yrange_
            x_min = max(0, min(x_min, n - 1)); x_max = max(0, min(x_max, n - 1))
            y_min = max(0, min(y_min, n - 1)); y_max = max(0, min(y_max, n - 1))
            if x_max < x_min: x_min, x_max = x_max, x_min
            if y_max < y_min: y_min, y_max = y_max, y_min

        count = (x_max - x_min + 1) * (y_max - y_min + 1)
        print(f"[plan] layer={layer.name} z={z} x=[{x_min},{x_max}] y=[{y_min},{y_max}] tiles={count}")
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                jobs.append((z, x, y))

    total = len(jobs)
    if total == 0:
        print(f"[done] no jobs for layer={layer.name}")
        return

    # simple rate limiter
    min_interval = 0.0 if qps <= 0 else (1.0 / qps)
    last_t = 0.0

    def rate_limit_wait():
        nonlocal last_t
        if min_interval <= 0:
            return
        now = time.perf_counter()
        wait = (last_t + min_interval) - now
        if wait > 0:
            time.sleep(wait)
        last_t = time.perf_counter()

    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ok = skip = nf404 = fail = 0

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency * 2)
    with httpx.Client(headers=headers, limits=limits, follow_redirects=True) as client:

        def fetch_one(z: int, x: int, y: int) -> str:
            url = render_mobac_url(layer, z, x, y)
            path = out_dir / str(z) / str(x) / f"{y}.{layer.tile_type}"
            if path.exists():
                return "skip"

            ensure_dir(path.parent)
            tmp = path.with_suffix(path.suffix + ".part")

            last = None
            for i in range(retries + 1):
                try:
                    rate_limit_wait()
                    r = client.get(url, timeout=timeout_s)
                    if r.status_code == 200 and r.content:
                        with open(tmp, "wb") as f:
                            f.write(r.content)
                        os.replace(tmp, path)
                        return "ok"
                    if r.status_code == 404:
                        return "404"
                    last = f"http_{r.status_code}"
                    time.sleep(backoff_s * (i + 1))
                except Exception as e:
                    last = repr(e)
                    time.sleep(backoff_s * (i + 1))
            return f"fail:{last}"

        print(f"[start] layer={layer.name} jobs={total} out={out_dir}")
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = [ex.submit(fetch_one, z, x, y) for (z, x, y) in jobs]
            done = 0
            for fu in as_completed(futs):
                res = fu.result()
                done += 1
                if res == "ok":
                    ok += 1
                elif res == "skip":
                    skip += 1
                elif res == "404":
                    nf404 += 1
                else:
                    fail += 1

                if done % 200 == 0 or done == total:
                    print(f"[prog] {done}/{total} ok={ok} skip={skip} 404={nf404} fail={fail}")

                if isinstance(res, str) and res.startswith("fail:"):
                    # 不刷屏：只偶尔打印
                    if done % 200 == 0:
                        print(f"[fail] sample: {res}")

        print(f"[finish] layer={layer.name} ok={ok} skip={skip} 404={nf404} fail={fail}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Download tiles from MOBAC XML map sources (XYZ).")
    ap.add_argument("--srcdir", required=True, help="地图源目录（包含 *.xml）")
    ap.add_argument("--source", default="", help="要使用的地图源名称（不填则列出并退出）")
    ap.add_argument("--out", required=False, default="./tiles_out", help="输出目录")
    ap.add_argument("--zooms", required=False, default="", help='zoom：如 "12" 或 "10-16" 或 "10-12,15"')
    ap.add_argument("--bbox", default="", help="下载范围 bbox：minLon,minLat,maxLon,maxLat（推荐）")
    ap.add_argument("--xrange", default="", help="x 范围：xmin,xmax（与 --yrange 配套）")
    ap.add_argument("--yrange", default="", help="y 范围：ymin,ymax（与 --xrange 配套）")
    ap.add_argument("--world", action="store_true", help="全世界（危险！必须显式开启）")
    ap.add_argument("--composite", action="store_true", help="对 multi-layer 源进行本地合成，输出单张 png（类似 MOBAC）")

    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--qps", type=float, default=20.0, help="限速 QPS（0=不限速）")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--backoff", type=float, default=0.6)
    ap.add_argument("--ua", default="MOBAC-XYZ-Downloader/1.0 (httpx)")
    ap.add_argument("--referer", default="")

    args = ap.parse_args()

    src_dir = Path(args.srcdir).expanduser().resolve()
    if not src_dir.exists():
        raise SystemExit(f"srcdir not exists: {src_dir}")

    sources = load_sources_from_dir(src_dir)
    if not sources:
        raise SystemExit(f"目录下没有解析到可用的 MOBAC XML: {src_dir}")

    if not args.source.strip():
        print("可用地图源：")
        for p, ms in sources:
            print(f"- {ms.name}   (file={p.name}, layers={len(ms.layers)})")
            for i, ly in enumerate(ms.layers):
                print(f"    [{i}] {ly.name} zoom=[{ly.min_zoom},{ly.max_zoom}] type={ly.tile_type}")
        print("\n用法示例：")
        print(f'  python mobac_source_tile_downloader.py --srcdir "{src_dir}" --source "Gaode Satellite + Label" --zooms "12-14" --bbox "123.35,41.65,123.55,41.85" --out "./out_tiles"')
        return 0

    # find source by name
    chosen: Optional[Tuple[Path, MapSource]] = None
    for p, ms in sources:
        if ms.name == args.source.strip():
            chosen = (p, ms)
            break
    if not chosen:
        raise SystemExit(f'找不到 source="{args.source}"，先不带 --source 运行一次查看列表')

    zooms = parse_zooms(args.zooms)
    if not zooms:
        raise SystemExit('必须提供 --zooms，例如 "12-14"')

    bbox = None
    if args.bbox.strip():
        parts = [float(x.strip()) for x in args.bbox.split(",")]
        if len(parts) != 4:
            raise SystemExit("--bbox 必须是 4 个数：minLon,minLat,maxLon,maxLat")
        bbox = (parts[0], parts[1], parts[2], parts[3])

    xrange_ = yrange_ = None
    if args.xrange.strip() or args.yrange.strip():
        if not (args.xrange.strip() and args.yrange.strip()):
            raise SystemExit("--xrange 和 --yrange 必须一起提供")
        xr = [int(x.strip()) for x in args.xrange.split(",")]
        yr = [int(x.strip()) for x in args.yrange.split(",")]
        if len(xr) != 2 or len(yr) != 2:
            raise SystemExit("--xrange/--yrange 格式：xmin,xmax / ymin,ymax")
        xrange_ = (xr[0], xr[1])
        yrange_ = (yr[0], yr[1])

    out_root = Path(args.out).expanduser().resolve()
    ensure_dir(out_root)

    headers = {"User-Agent": args.ua}
    if args.referer.strip():
        headers["Referer"] = args.referer.strip()

    xml_path, ms = chosen
    print(f"[use] source={ms.name} file={xml_path}")

    layer_out_dirs: List[Path] = []

    for idx, layer in enumerate(ms.layers):
        layer_out = out_root / ms.name / f"layer_{idx}_{layer.name}"
        layer_out_dirs.append(layer_out)

        download_range_for_layer(
            layer, zooms, layer_out,
            bbox=bbox,
            xrange_=xrange_,
            yrange_=yrange_,
            world=args.world,
            concurrency=max(1, args.concurrency),
            timeout_s=max(1.0, args.timeout),
            retries=max(0, args.retries),
            backoff_s=max(0.1, args.backoff),
            qps=max(0.0, args.qps),
            headers=headers
        )

    # ✅ 下载完后合成
    if args.composite and len(layer_out_dirs) >= 2:
        merged_out = out_root / ms.name / "merged"
        print(f"[composite] start -> {merged_out}")
        composite_tiles_for_layers(layer_out_dirs, zooms, merged_out)
        print(f"[composite] finished -> {merged_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
