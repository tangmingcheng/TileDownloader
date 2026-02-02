from __future__ import annotations

import os
import sys
import shlex
import subprocess
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="MOBAC 瓦片下载器 UI", layout="wide")

st.title("MOBAC XML 瓦片下载器（XYZ）")
st.caption("UI 通过 subprocess 调用 mobac_source_tile_downloader.py；支持 multi-layer 合成。")

# 尽量定位同目录脚本
DEFAULT_SCRIPT = Path(__file__).resolve().parent / "mobac_source_tile_downloader.py"

def normalize_path(p: str) -> Path:
    return Path(p).expanduser().resolve()

def run_cmd_realtime(cmd: list[str]):
    """实时跑子进程并把 stdout/stderr 输出到 Streamlit"""
    st.code(" ".join(shlex.quote(x) for x in cmd), language="bash")

    log_area = st.empty()
    lines = []

    # Windows 下用 text=True + bufsize=1 + universal_newlines
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=os.environ.copy(),
    )

    try:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            # 控制 UI 不要无限长（保留最近 400 行）
            if len(lines) > 400:
                lines = lines[-400:]
            log_area.code("\n".join(lines), language="text")
        rc = p.wait()
    finally:
        try:
            p.kill()
        except Exception:
            pass

    if rc == 0:
        st.success("执行完成（exit code 0）")
    else:
        st.error(f"执行失败（exit code {rc}）")

    return rc


# ---------------- UI: basic paths ----------------
with st.sidebar:
    st.header("路径设置")
    script_path = st.text_input("下载脚本路径", value=str(DEFAULT_SCRIPT))
    srcdir = st.text_input("地图源目录（包含 *.xml）", value=str(Path.cwd()))
    outdir = st.text_input("输出目录", value=str(Path.cwd() / "out_tiles"))

script_path_p = normalize_path(script_path)
srcdir_p = normalize_path(srcdir)
outdir_p = normalize_path(outdir)

if not script_path_p.exists():
    st.error(f"找不到脚本：{script_path_p}")
    st.stop()

if not srcdir_p.exists():
    st.error(f"地图源目录不存在：{srcdir_p}")
    st.stop()

# 先调用脚本列源：`--srcdir xxx` 不带 --source 会打印列表
st.subheader("1) 读取地图源列表")
col_a, col_b = st.columns([1, 1])
with col_a:
    refresh = st.button("刷新地图源列表")
with col_b:
    st.caption("如果列表为空：确认 XML 在“地图源目录”下，且是 MOBAC 的 customMapSource/customMultiLayerMapSource 格式。")

# 简单缓存：用 session_state 存列表输出
if "source_list_text" not in st.session_state or refresh:
    cmd = [sys.executable, str(script_path_p), "--srcdir", str(srcdir_p)]
    # 这里不实时显示，直接跑完抓输出
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    st.session_state.source_list_text = p.stdout

source_list_text = st.session_state.source_list_text
st.code(source_list_text, language="text")

# 从输出里粗暴提取 “- xxx (file=...)” 这类行
sources = []
for line in source_list_text.splitlines():
    line = line.strip()
    if line.startswith("- "):
        # 取到文件信息前： "- NAME   (file=..."
        name = line[2:]
        if "  (file=" in name:
            name = name.split("  (file=", 1)[0].strip()
        sources.append(name)

if not sources:
    st.warning("未解析到地图源名称。请确认 srcdir 下有可解析的 MOBAC XML。")
    st.stop()

st.subheader("2) 参数设置")

col1, col2 = st.columns([1, 1])

with col1:
    source_name = st.selectbox("选择地图源（--source）", options=sources)
    zooms = st.text_input('Zoom（例如 "16" 或 "12-14" 或 "10-12,15"）', value="16")

    range_mode = st.radio("范围模式", options=["bbox（经纬度）", "x/y 范围（瓦片坐标）"], horizontal=True)

    if range_mode.startswith("bbox"):
        bbox = st.text_input("bbox：minLon,minLat,maxLon,maxLat", value="")
        st.caption("建议优先 bbox。注意：bbox 坐标必须与瓦片源坐标系一致，否则会整体偏移。")
        xrange_ = ""
        yrange_ = ""
    else:
        bbox = ""
        xrange_ = st.text_input("xrange：xmin,xmax", value="54200,54400")
        yrange_ = st.text_input("yrange：ymin,ymax", value="24800,25000")

with col2:
    st.markdown("### 合成（MultiLayer）")
    composite = st.checkbox("合成输出 merged（类似 MOBAC）", value=True)

    st.markdown("### 性能/稳定")
    concurrency = st.slider("并发数", 1, 128, 24, 1)
    qps = st.number_input("限速 QPS（0=不限速）", min_value=0.0, max_value=2000.0, value=20.0, step=1.0)
    timeout_s = st.number_input("超时（秒）", min_value=1.0, max_value=120.0, value=15.0, step=1.0)
    retries = st.slider("重试次数", 0, 10, 3, 1)
    backoff = st.number_input("退避系数（秒）", min_value=0.1, max_value=5.0, value=0.6, step=0.1)

    ua = st.text_input("User-Agent", value="MOBAC-XYZ-Downloader/1.0 (Streamlit)")
    referer = st.text_input("Referer（可选）", value="")

st.divider()

st.subheader("3) 执行")

# 组装命令
def build_cmd() -> list[str]:
    cmd = [sys.executable, "-u", str(script_path_p)]
    cmd += ["--srcdir", str(srcdir_p)]
    cmd += ["--source", source_name]
    cmd += ["--zooms", zooms]
    cmd += ["--out", str(outdir_p)]

    if bbox.strip():
        cmd += ["--bbox", bbox.strip()]
    else:
        if xrange_.strip() and yrange_.strip():
            cmd += ["--xrange", xrange_.strip(), "--yrange", yrange_.strip()]

    cmd += ["--concurrency", str(int(concurrency))]
    cmd += ["--qps", str(float(qps))]
    cmd += ["--timeout", str(float(timeout_s))]
    cmd += ["--retries", str(int(retries))]
    cmd += ["--backoff", str(float(backoff))]
    cmd += ["--ua", ua.strip()]

    if referer.strip():
        cmd += ["--referer", referer.strip()]

    if composite:
        cmd += ["--composite"]

    return cmd

run = st.button("开始运行", type="primary")

if run:
    # 基础校验：范围必须有一个
    if (not bbox.strip()) and (not (xrange_.strip() and yrange_.strip())):
        st.error("必须提供 bbox 或同时提供 xrange/yrange。")
        st.stop()

    outdir_p.mkdir(parents=True, exist_ok=True)

    cmd = build_cmd()
    run_cmd_realtime(cmd)
