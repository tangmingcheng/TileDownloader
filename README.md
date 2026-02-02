# 🗺️ TileDownloader

一个基于 **MOBAC 地图源 XML** 的高性能 XYZ 瓦片下载工具，支持多图层叠加、本地合成，适用于离线地图制作、GIS 开发与自建瓦片服务。

支持解析 MOBAC 的 `customMapSource` 和 `customMultiLayerMapSource`，可按经纬度范围或 XYZ 范围下载瓦片，并可对多图层进行本地 PNG 透明合成。

---

## ✨ 功能特性

- 解析 MOBAC XML 地图源  
- 支持 XYZ 标准 WebMercator 瓦片计算  
- 支持以下下载范围方式：
  - 经纬度范围（推荐）
  - 指定 x/y 范围
  - 全世界（需显式开启）
- 多线程并发下载  
- QPS 限速控制（防封）  
- 自动重试与退避机制  
- 支持多图层地图源（底图 + 标注等）  
- 本地 PNG Alpha 合成输出  

---

## 🗂 输出目录结构示例

下载并合成后的目录结构：

```
tiles_out/
└── Gaode Satellite + Label/
    ├── layer_0_Gaode Satellite/
    │   └── z/x/y.png
    ├── layer_1_Label/
    │   └── z/x/y.png
    └── merged/
        └── z/x/y.png
```

---

## 🚀 使用方法

### 1️⃣ 列出可用地图源

```bash
python mobac_source_tile_downloader.py --srcdir ./mobac_sources
```

---

### 2️⃣ 按经纬度范围下载（推荐）

```bash
python mobac_source_tile_downloader.py   --srcdir ./mobac_sources   --source "Gaode Satellite + Label"   --zooms "12-14"   --bbox "123.35,41.65,123.55,41.85"   --out ./tiles_out   --composite
```

---

### 3️⃣ 指定 XYZ 范围下载

```bash
python mobac_source_tile_downloader.py   --srcdir ./mobac_sources   --source "OSM Mapnik"   --zooms "10-12"   --xrange "1680,1690"   --yrange "780,790"
```

---

### 4️⃣ 下载全世界（⚠ 极度危险）

```bash
python mobac_source_tile_downloader.py   --srcdir ./mobac_sources   --source "OSM Mapnik"   --zooms "0-5"   --world
```

---

## ⚙️ 参数说明

| 参数 | 说明 |
|------|------|
| `--srcdir` | MOBAC XML 地图源目录 |
| `--source` | 地图源名称（必须和 XML 中 name 一致） |
| `--zooms` | 下载级别，例如 `12` 或 `10-16` |
| `--bbox` | 经纬度范围：minLon,minLat,maxLon,maxLat |
| `--xrange/--yrange` | 指定 XYZ 范围 |
| `--world` | 全世界下载（需显式开启） |
| `--concurrency` | 并发线程数（默认 24） |
| `--qps` | 请求限速（默认 20 次/秒） |
| `--timeout` | 单请求超时时间（秒） |
| `--retries` | 失败重试次数 |
| `--backoff` | 重试退避时间（秒） |
| `--ua` | 自定义 User-Agent |
| `--referer` | 自定义 Referer |
| `--composite` | 多图层下载后本地合成 PNG |

---

## 🧠 瓦片坐标规则

本工具使用标准 **WebMercator XYZ 瓦片规则**：

- 坐标系：EPSG:3857  
- 瓦片格式：`/{z}/{x}/{y}.png`  
- 原点：左上角  
- Y 轴方向：向下递增（XYZ 标准）  

---

## 🧩 多图层合成说明

对于 `customMultiLayerMapSource`：

1. 分别下载每个图层  
2. 按顺序进行 Alpha 叠加  
3. 输出到 `merged/` 目录  

叠加顺序：

```
第一个图层 = 底图
后续图层 = 覆盖层（例如道路标注）
```

---

## ⚠️ 使用须知

- 请遵守地图服务提供方的使用协议  
- 不要高并发、无限下载，容易被封 IP  
- 推荐合理设置 `--qps` 限速  
- 商业用途请确保拥有合法授权  

---

## 📜 License

MIT License
