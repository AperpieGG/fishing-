#!/usr/bin/env python3

import argparse
import math
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, request, send_file


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BATHYMETRY_PATH = PROJECT_ROOT / "data" / "emodnet_greece_tiles"
DEFAULT_HIGH_RES_BATHYMETRY_PATH = PROJECT_ROOT / "data" / "emodnet_hr_bathymetry"
DEFAULT_REGIONAL_BATHYMETRY_PATH = PROJECT_ROOT / "data" / "emodnet_athens_saronic_2024.tif"
DEFAULT_REGIONAL_BATHYMETRY_NETCDF_PATH = PROJECT_ROOT / "data" / "emodnet_athens_saronic_2024.nc"
DEFAULT_GEBCO_BATHYMETRY_PATH = PROJECT_ROOT / "data" / "gebco_2026.nc"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5050
DEFAULT_TIMEZONE = "Europe/Athens"
MARINE_API_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
EMODNET_WFS_URL = "https://ows.emodnet-bathymetry.eu/wfs"
EMODNET_DEPTH_API_URL = "https://rest.emodnet-bathymetry.eu/depth_sample"
WORLD_BOUNDS = (-180.0, -85.05112878, 180.0, 85.05112878)
# The EMODnet point service is intended for European sea regions.
EMODNET_POINT_BOUNDS = (-36.0, 25.0, 42.0, 85.0)
MIN_RELIABLE_DEPTH_M = 0.05


def default_regional_bathymetry_path():
    for path in (DEFAULT_REGIONAL_BATHYMETRY_PATH, DEFAULT_REGIONAL_BATHYMETRY_NETCDF_PATH):
        if path.is_file():
            return str(path)
    return None


app = Flask(__name__)
app.config["BATHYMETRY_PATH"] = (
    str(DEFAULT_BATHYMETRY_PATH) if DEFAULT_BATHYMETRY_PATH.is_dir() else None
)
app.config["HIGH_RES_BATHYMETRY_PATH"] = (
    str(DEFAULT_HIGH_RES_BATHYMETRY_PATH)
    if DEFAULT_HIGH_RES_BATHYMETRY_PATH.is_dir()
    else None
)
app.config["REGIONAL_BATHYMETRY_PATH"] = (
    default_regional_bathymetry_path()
)
app.config["GEBCO_BATHYMETRY_PATH"] = (
    str(DEFAULT_GEBCO_BATHYMETRY_PATH)
    if DEFAULT_GEBCO_BATHYMETRY_PATH.is_file()
    else None
)
app.config["TIMEZONE"] = DEFAULT_TIMEZONE
app.config["REMOTE_DEPTH_ENABLED"] = True


PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ocean Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="">
  <style>
    :root {
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #172b34;
      background: #dfecef;
    }
    * { box-sizing: border-box; }
    body { margin: 0; }
    #map { height: 100vh; width: 100vw; }
    .panel {
      position: absolute;
      z-index: 1000;
      top: 16px;
      right: 16px;
      width: min(370px, calc(100vw - 32px));
      max-height: calc(100vh - 32px);
      overflow: auto;
      padding: 15px;
      border: 1px solid rgba(112, 142, 148, 0.45);
      border-radius: 8px;
      background: rgba(247, 251, 251, 0.96);
      box-shadow: 0 14px 36px rgba(20, 58, 70, 0.22);
    }
    .eyebrow {
      margin: 0 0 3px;
      color: #55727a;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      color: #10252d;
      font-size: 21px;
      line-height: 1.15;
    }
    .toolbar {
      display: flex;
      gap: 7px;
      margin: 14px 0 11px;
    }
    .tool-button {
      min-height: 36px;
      padding: 7px 12px;
      border: 1px solid #aac2c6;
      border-radius: 5px;
      background: #ffffff;
      color: #24434d;
      cursor: pointer;
      font: inherit;
      font-size: 13px;
      font-weight: 600;
    }
    .tool-button:hover, .tool-button:focus-visible { border-color: #167f91; }
    .tool-button.active {
      border-color: #126c7b;
      background: #126c7b;
      color: #ffffff;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .metric {
      min-height: 62px;
      padding: 9px;
      border: 1px solid #d3e1e3;
      border-radius: 6px;
      background: #ffffff;
    }
    .metric.wide { grid-column: 1 / -1; }
    .label {
      display: block;
      margin-bottom: 4px;
      color: #5d747a;
      font-size: 11px;
      line-height: 1.2;
    }
    .value {
      display: block;
      overflow-wrap: anywhere;
      color: #17323b;
      font-size: 17px;
      font-weight: 700;
      line-height: 1.25;
    }
    .section-label {
      margin: 14px 0 7px;
      color: #47656d;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .status {
      margin-top: 11px;
      padding-top: 10px;
      border-top: 1px solid #d8e4e5;
      color: #557079;
      font-size: 12px;
      line-height: 1.35;
    }
    .legend {
      position: absolute;
      z-index: 1000;
      bottom: 18px;
      left: 18px;
      padding: 9px;
      border: 1px solid rgba(112, 142, 148, 0.45);
      border-radius: 6px;
      background: rgba(247, 251, 251, 0.94);
      box-shadow: 0 8px 20px rgba(20, 58, 70, 0.16);
      color: #294850;
      font-size: 11px;
    }
    .scale {
      width: 190px;
      height: 11px;
      margin: 6px 0 4px;
      background: linear-gradient(90deg, #b9f1ff, #5fc8e2, #2382c4, #173f86, #090f3f);
    }
    .scale-labels { display: flex; justify-content: space-between; }
    .measure-status { display: none; color: #126c7b; font-size: 12px; }
    .measure-status.visible { display: block; }
    .wind-canvas { pointer-events: none; }
    .leaflet-control-layers { border: 1px solid rgba(112, 142, 148, 0.45); }
    .leaflet-container.map-measuring { cursor: crosshair; }
    @media (max-width: 680px) {
      .panel {
        top: auto;
        right: 10px;
        bottom: 10px;
        left: 10px;
        width: auto;
        max-height: 49vh;
        padding: 12px;
      }
      .metric-grid { grid-template-columns: 1fr 1fr; }
      .legend { bottom: auto; top: 10px; left: 10px; }
      .leaflet-control-layers { max-width: 180px; }
    }
    @media (max-width: 390px) {
      .metric-grid { grid-template-columns: 1fr; }
      .metric.wide { grid-column: auto; }
      .toolbar { flex-wrap: wrap; }
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <section class="panel" aria-live="polite">
    <p class="eyebrow">Bathymetry explorer</p>
    <h1>Ocean Map</h1>
    <div class="toolbar" aria-label="Map tools">
      <button id="inspectButton" class="tool-button active" type="button" aria-pressed="true">Inspect depth</button>
      <button id="measureButton" class="tool-button" type="button" aria-pressed="false">Measure distance</button>
      <button id="windButton" class="tool-button" type="button" aria-pressed="false">Animate wind</button>
      <button id="clearButton" class="tool-button" type="button">Clear</button>
    </div>
    <div id="measureStatus" class="measure-status">Select a coastline point, then a target point.</div>
    <div class="section-label">Pointer</div>
    <div class="metric-grid">
      <div class="metric">
        <span class="label">Depth under cursor</span>
        <span id="pointerDepth" class="value">Move over water</span>
      </div>
      <div class="metric">
        <span class="label">Cursor position</span>
        <span id="pointerPosition" class="value">n/a</span>
      </div>
    </div>
    <div class="section-label">Wind field</div>
    <div class="metric-grid">
      <div class="metric">
        <span class="label">Wind at 5 m</span>
        <span id="windFieldSpeed" class="value">Animation off</span>
      </div>
      <div class="metric">
        <span class="label">Beaufort</span>
        <span id="windFieldBeaufort" class="value">n/a</span>
      </div>
    </div>
    <div class="section-label">Selected point</div>
    <div id="results" class="metric-grid">
      <div class="metric wide">
        <span class="label">Status</span>
        <span class="value">Click the map</span>
      </div>
    </div>
    <div class="section-label">Measurement</div>
    <div class="metric-grid">
      <div class="metric wide">
        <span class="label">Distance</span>
        <span id="distanceValue" class="value">No measurement</span>
      </div>
    </div>
    <div id="status" class="status">Global bathymetry layer loading.</div>
  </section>
  <div class="legend" aria-label="Bathymetry depth legend">
    <div>Water depth</div>
    <div class="scale"></div>
    <div class="scale-labels"><span>0.5 m</span><span>10 m</span><span>100 m</span><span>500+ m</span></div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const worldBounds = L.latLngBounds([ -85.05112878, -180 ], [ 85.05112878, 180 ]);
    const map = L.map("map", {
      zoomControl: true,
      preferCanvas: true,
      maxBounds: worldBounds,
      maxBoundsViscosity: 0.35,
      minZoom: 2,
      worldCopyJump: false
    }).setView([20, 0], 2);

    const streetLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      noWrap: true,
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);
    const satelliteLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        noWrap: true,
        attribution: "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community"
      }
    );
    const globalBathymetryLayer = L.tileLayer.wms("https://ows.emodnet-bathymetry.eu/wms", {
      layers: "emodnet:mean_multicolour",
      format: "image/png",
      transparent: true,
      version: "1.1.1",
      opacity: 0.68,
      attribution: "Bathymetry: EMODnet Bathymetry"
    });
    const globalAtlasLayer = L.tileLayer.wms("https://ows.emodnet-bathymetry.eu/wms", {
      layers: "emodnet:mean_atlas_land",
      format: "image/png",
      transparent: true,
      version: "1.1.1",
      opacity: 0.62,
      attribution: "Bathymetry: EMODnet Bathymetry / GEBCO"
    }).addTo(map);

    const overlayLayers = {
      "Global atlas depth": globalAtlasLayer,
      "Regional depth colours": globalBathymetryLayer
    };
    const bathymetryLayer = L.layerGroup();
    const hrContourLayer = L.layerGroup();
    const emodnetContourWmsLayer = L.tileLayer.wms("https://ows.emodnet-bathymetry.eu/wms", {
      layers: "emodnet:contours",
      format: "image/png",
      transparent: true,
      version: "1.3.0",
      opacity: 0.86,
      attribution: "Contours: EMODnet Bathymetry"
    });
    overlayLayers["Local DTM detail"] = bathymetryLayer;
    overlayLayers["Local high-resolution contours"] = hrContourLayer;
    overlayLayers["EMODnet contour chart"] = emodnetContourWmsLayer;
    L.control.layers(
      { "Street map": streetLayer, "Satellite": satelliteLayer },
      overlayLayers,
      { collapsed: false, position: "topleft" }
    ).addTo(map);

    let hrContourRequest = null;
    let localDataAvailable = false;

    fetch("/api/bathymetry-overlays")
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        if (!data) {
          setStatus("Global layer ready. Local raster detail is not configured.");
          return;
        }
        localDataAvailable = true;
        for (const tile of data.tiles) {
          L.imageOverlay(tile.url, [[tile.south, tile.west], [tile.north, tile.east]], {
            opacity: 0.46,
            interactive: false
          }).addTo(bathymetryLayer);
        }
        bathymetryLayer.addTo(map);
        hrContourLayer.addTo(map);
        setStatus(`${data.tiles.length} local DTM tiles ready. High-resolution contours appear when zoomed in.`);
        refreshHrContours();
      })
      .catch(() => setStatus("Global layer ready. Local raster detail is unavailable."));

    function contourStyle(depth, highResolution = false) {
      const major = depth % 100 === 0;
      return {
        color: highResolution ? (major ? "#8a3b18" : "#d56b22") : (major ? "#082c55" : "#176c9b"),
        weight: highResolution ? (major ? 2.5 : 1.5) : (major ? 2.2 : 1.2),
        opacity: highResolution ? 0.96 : (major ? 0.95 : 0.78)
      };
    }

    function renderContourLine(layer, line, depth, source, highResolution = false) {
      const polyline = L.polyline(line.map((point) => [point[0], point[1]]), contourStyle(depth, highResolution));
      polyline.bindTooltip(`${Number(depth).toFixed(0)} m${source ? ` (${source})` : ""}`, {
        sticky: true,
        direction: "top",
        opacity: 0.92
      });
      polyline.addTo(layer);
    }

    function refreshHrContours() {
      const showHr = map.hasLayer(hrContourLayer);
      if (!showHr || !localDataAvailable) return;
      if (map.getZoom() < 10) {
        hrContourLayer.clearLayers();
        return;
      }
      if (hrContourRequest) hrContourRequest.abort();
      hrContourRequest = new AbortController();
      const bounds = map.getBounds();
      const query = new URLSearchParams({
        west: bounds.getWest().toFixed(6),
        south: bounds.getSouth().toFixed(6),
        east: bounds.getEast().toFixed(6),
        north: bounds.getNorth().toFixed(6),
        hr: "1",
        coarse: "0"
      });
      fetch(`/api/depth-contours?${query.toString()}`, { signal: hrContourRequest.signal })
        .then((response) => response.ok ? response.json() : null)
        .then((data) => {
          if (!data) return;
          hrContourLayer.clearLayers();
          for (const feature of data.contours || []) {
            renderContourLine(hrContourLayer, feature.line, feature.depth_m, feature.label, true);
          }
        })
        .catch((error) => {
          if (error.name !== "AbortError") console.warn(error);
        });
    }

    map.on("moveend", refreshHrContours);
    map.on("overlayadd overlayremove", refreshHrContours);

    const WindAnimationLayer = L.Layer.extend({
      initialize: function () {
        this._field = null;
        this._particles = [];
        this._animationFrame = null;
      },
      onAdd: function (mapInstance) {
        this._map = mapInstance;
        this._canvas = L.DomUtil.create("canvas", "wind-canvas");
        this._canvas.setAttribute("aria-hidden", "true");
        this._map.getPanes().overlayPane.appendChild(this._canvas);
        this._map.on("move zoom resize", this._reset, this);
        this._reset();
        this._animationFrame = requestAnimationFrame(() => this._draw());
      },
      onRemove: function () {
        this._map.off("move zoom resize", this._reset, this);
        if (this._animationFrame) cancelAnimationFrame(this._animationFrame);
        if (this._canvas) this._canvas.remove();
        this._canvas = null;
      },
      setField: function (field) {
        this._field = field;
        this._seedParticles();
      },
      _reset: function () {
        if (!this._canvas) return;
        const size = this._map.getSize();
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const topLeft = this._map.containerPointToLayerPoint([0, 0]);
        this._topLeft = topLeft;
        L.DomUtil.setPosition(this._canvas, topLeft);
        this._canvas.style.width = `${size.x}px`;
        this._canvas.style.height = `${size.y}px`;
        this._canvas.width = Math.round(size.x * ratio);
        this._canvas.height = Math.round(size.y * ratio);
        this._context = this._canvas.getContext("2d");
        this._context.setTransform(ratio, 0, 0, ratio, 0, 0);
        this._seedParticles();
      },
      _seedParticles: function () {
        if (!this._map || !this._canvas) return;
        const size = this._map.getSize();
        const count = Math.max(180, Math.min(520, Math.round(size.x * size.y / 4500)));
        this._particles = Array.from({ length: count }, () => ({
          x: Math.random() * size.x,
          y: Math.random() * size.y,
          age: Math.random() * 90,
          maxAge: 55 + Math.random() * 90
        }));
      },
      _sample: function (lat, lng) {
        if (!this._field || !this._field.points) return null;
        if (lat < this._field.south || lat > this._field.north || lng < this._field.west || lng > this._field.east) return null;
        const column = Math.max(0, Math.min(this._field.columns - 1, Math.round((lng - this._field.west) / (this._field.east - this._field.west) * (this._field.columns - 1))));
        const row = Math.max(0, Math.min(this._field.rows - 1, Math.round((lat - this._field.south) / (this._field.north - this._field.south) * (this._field.rows - 1))));
        return this._field.points[row * this._field.columns + column];
      },
      _resetParticle: function (particle) {
        const size = this._map.getSize();
        particle.x = Math.random() * size.x;
        particle.y = Math.random() * size.y;
        particle.age = 0;
        particle.maxAge = 55 + Math.random() * 90;
      },
      _draw: function () {
        if (!this._canvas || !this._context) return;
        this._animationFrame = requestAnimationFrame(() => this._draw());
        const size = this._map.getSize();
        const context = this._context;
        context.globalCompositeOperation = "destination-out";
        context.fillStyle = "rgba(0, 0, 0, 0.12)";
        context.fillRect(0, 0, size.x, size.y);
        if (!this._field) {
          context.globalCompositeOperation = "source-over";
          return;
        }
        context.globalCompositeOperation = "source-over";
        context.lineWidth = 1.25;
        context.strokeStyle = "rgba(18, 108, 123, 0.78)";
        for (const particle of this._particles) {
          const layerPoint = L.point(particle.x, particle.y).add(this._topLeft);
          const latlng = this._map.layerPointToLatLng(layerPoint);
          const wind = this._sample(latlng.lat, latlng.lng);
          if (!wind || wind.speed_5m_kmh <= 0) {
            this._resetParticle(particle);
            continue;
          }
          const speedPixels = Math.max(0.75, Math.min(3.4, wind.speed_5m_kmh / 14));
          const speed = Math.max(wind.speed_5m_kmh, 0.01);
          const unitX = wind.eastward_kmh / speed;
          const unitY = -wind.northward_kmh / speed;
          const dx = unitX * speedPixels;
          const dy = unitY * speedPixels;
          const nextX = particle.x + dx;
          const nextY = particle.y + dy;
          const arrowLength = Math.max(8, Math.min(18, 5 + wind.speed_5m_kmh / 4));
          const tailX = particle.x - unitX * arrowLength / 2;
          const tailY = particle.y - unitY * arrowLength / 2;
          const headX = particle.x + unitX * arrowLength / 2;
          const headY = particle.y + unitY * arrowLength / 2;
          const headSize = Math.max(3.5, Math.min(7, arrowLength * 0.42));
          const leftHeadX = headX - unitX * headSize - unitY * headSize * 0.72;
          const leftHeadY = headY - unitY * headSize + unitX * headSize * 0.72;
          const rightHeadX = headX - unitX * headSize + unitY * headSize * 0.72;
          const rightHeadY = headY - unitY * headSize - unitX * headSize * 0.72;
          const drawArrow = () => {
            context.beginPath();
            context.moveTo(tailX, tailY);
            context.lineTo(headX, headY);
            context.moveTo(leftHeadX, leftHeadY);
            context.lineTo(headX, headY);
            context.lineTo(rightHeadX, rightHeadY);
            context.stroke();
          };
          context.lineCap = "round";
          context.lineJoin = "round";
          context.strokeStyle = "rgba(7, 38, 50, 0.72)";
          context.lineWidth = 4;
          drawArrow();
          context.strokeStyle = "rgba(255, 255, 255, 0.97)";
          context.lineWidth = 2.2;
          drawArrow();
          particle.x = nextX;
          particle.y = nextY;
          particle.age += 1;
          if (particle.age > particle.maxAge || nextX < -8 || nextY < -8 || nextX > size.x + 8 || nextY > size.y + 8) {
            this._resetParticle(particle);
          }
        }
      }
    });

    const windLayer = new WindAnimationLayer();
    let windLayerEnabled = false;
    let windFieldRequest = null;

    async function refreshWindField() {
      if (!windLayerEnabled) return;
      if (windFieldRequest) windFieldRequest.abort();
      windFieldRequest = new AbortController();
      const bounds = map.getBounds();
      const query = new URLSearchParams({
        west: bounds.getWest().toFixed(5),
        south: bounds.getSouth().toFixed(5),
        east: bounds.getEast().toFixed(5),
        north: bounds.getNorth().toFixed(5)
      });
      try {
        const response = await fetch(`/api/wind-field?${query.toString()}`, { signal: windFieldRequest.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Wind request failed");
        windLayer.setField(data);
        const representative = data.representative;
        document.getElementById("windFieldSpeed").textContent = representative
          ? `${Number(representative.speed_5m_kmh).toFixed(1)} km/h`
          : "Unavailable";
        document.getElementById("windFieldBeaufort").textContent = representative?.beaufort_label || "n/a";
        setStatus(`Wind animation: estimated 5 m wind, model time ${data.time || "n/a"}.`);
      } catch (error) {
        if (error.name !== "AbortError") setStatus(`Wind animation unavailable: ${error.message}`);
      }
    }

    let windFieldTimer = null;
    function scheduleWindFieldRefresh() {
      if (!windLayerEnabled) return;
      clearTimeout(windFieldTimer);
      windFieldTimer = setTimeout(() => refreshWindField(), 550);
    }

    map.on("moveend", scheduleWindFieldRefresh);

    let selectedMarker = null;
    let pointerRequest = null;
    let pointerTimer = null;
    let lastPointerContainerPoint = null;
    let selectedPointRequest = null;
    let toolMode = "inspect";
    const measureLayer = L.layerGroup().addTo(map);
    let measurePoints = [];

    function fmt(value, unit, digits = 1) {
      if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
      return `${Number(value).toFixed(digits)} ${unit}`;
    }

    function setStatus(text) {
      document.getElementById("status").textContent = text;
    }

    function setTool(mode) {
      toolMode = mode;
      const inspect = mode === "inspect";
      document.getElementById("inspectButton").classList.toggle("active", inspect);
      document.getElementById("inspectButton").setAttribute("aria-pressed", String(inspect));
      document.getElementById("measureButton").classList.toggle("active", !inspect);
      document.getElementById("measureButton").setAttribute("aria-pressed", String(!inspect));
      document.getElementById("measureStatus").classList.toggle("visible", !inspect);
      map.getContainer().classList.toggle("map-measuring", !inspect);
      if (!inspect) {
        measurePoints = [];
        measureLayer.clearLayers();
        document.getElementById("distanceValue").textContent = "No measurement";
        setStatus("Measure mode ready.");
      }
    }

    function renderSelected(data) {
      const rows = [
        ["Selected point", `${data.latitude.toFixed(5)}, ${data.longitude.toFixed(5)}`, "wide"],
        ["Depth", data.depth_label || "n/a", ""],
        ["Depth source", data.depth_source || "No data", ""],
        ["Sea temperature", fmt(data.sea_surface_temperature_c, "C"), ""],
        ["Wave height", fmt(data.wave_height_m, "m"), ""],
        ["Wind at 5 m", fmt(data.wind_speed_5m_kmh, "km/h"), ""],
        ["Beaufort", data.beaufort_label || "n/a", ""],
        ["Wind direction", data.wind_direction_cardinal ? `${data.wind_direction_cardinal} (${fmt(data.wind_direction_5m_deg, "deg", 0)})` : fmt(data.wind_direction_5m_deg, "deg", 0), "wide"]
      ];
      document.getElementById("results").innerHTML = rows.map(([label, value, cls]) => `
        <div class="metric ${cls}"><span class="label">${label}</span><span class="value">${value}</span></div>
      `).join("");
    }

    function renderPointer(data, lat, lng) {
      document.getElementById("pointerDepth").textContent = data.depth_label || "No depth data";
      document.getElementById("pointerPosition").textContent = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
    }

    async function inspectDepth(lat, lng, updatePointer = false) {
      const controller = new AbortController();
      if (pointerRequest) pointerRequest.abort();
      pointerRequest = controller;
      const response = await fetch(`/api/depth?lat=${lat.toFixed(6)}&lon=${lng.toFixed(6)}&remote=0`, { signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Depth request failed");
      if (updatePointer) renderPointer(data, lat, lng);
      return data;
    }

    map.on("mousemove", (event) => {
      if (toolMode !== "inspect") return;
      const { lat, lng } = event.latlng;
      document.getElementById("pointerPosition").textContent = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
      const point = event.containerPoint;
      if (lastPointerContainerPoint) {
        const dx = point.x - lastPointerContainerPoint.x;
        const dy = point.y - lastPointerContainerPoint.y;
        if (dx * dx + dy * dy < 144) return;
      }
      lastPointerContainerPoint = point;
      clearTimeout(pointerTimer);
      pointerTimer = setTimeout(() => {
        inspectDepth(lat, lng, true).catch((error) => {
          if (error.name !== "AbortError") document.getElementById("pointerDepth").textContent = "Unavailable";
        });
      }, 420);
    });

    map.on("mouseout", () => {
      clearTimeout(pointerTimer);
      if (pointerRequest) pointerRequest.abort();
      lastPointerContainerPoint = null;
    });

    function haversineMeters(first, second) {
      const radius = 6371008.8;
      const toRadians = (degrees) => degrees * Math.PI / 180;
      const lat1 = toRadians(first.lat);
      const lat2 = toRadians(second.lat);
      const dLat = lat2 - lat1;
      const dLon = toRadians(second.lng - first.lng);
      const value = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
      return 2 * radius * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
    }

    function formatDistance(meters) {
      return meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(2)} km`;
    }

    function addMeasurePoint(latlng) {
      if (measurePoints.length === 2) {
        measurePoints = [];
        measureLayer.clearLayers();
      }
      measurePoints.push(latlng);
      L.circleMarker(latlng, { radius: 6, color: "#a44716", fillColor: "#f0a04b", fillOpacity: 1, weight: 2 }).addTo(measureLayer);
      if (measurePoints.length === 1) {
        document.getElementById("distanceValue").textContent = "Select target point";
        setStatus("Start point set.");
        return;
      }
      const distance = haversineMeters(measurePoints[0], measurePoints[1]);
      L.polyline(measurePoints, { color: "#a44716", weight: 3, dashArray: "7 5" }).addTo(measureLayer);
      document.getElementById("distanceValue").textContent = formatDistance(distance);
      setStatus("Distance measured between the two selected points.");
    }

    map.on("click", async (event) => {
      const { lat, lng } = event.latlng;
      if (toolMode === "measure") {
        addMeasurePoint(event.latlng);
        return;
      }
      if (selectedMarker) selectedMarker.remove();
      selectedMarker = L.marker([lat, lng]).addTo(map);
      setStatus("Loading selected point...");
      if (selectedPointRequest) selectedPointRequest.abort();
      selectedPointRequest = new AbortController();
      try {
        const response = await fetch(`/api/point?lat=${lat.toFixed(6)}&lon=${lng.toFixed(6)}`, { signal: selectedPointRequest.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Point request failed");
        renderPointer(data, lat, lng);
        renderSelected(data);
        setStatus(data.source_note);
      } catch (error) {
        if (error.name !== "AbortError") setStatus(`Error: ${error.message}`);
      }
    });

    document.getElementById("inspectButton").addEventListener("click", () => setTool("inspect"));
    document.getElementById("measureButton").addEventListener("click", () => setTool("measure"));
    document.getElementById("windButton").addEventListener("click", async () => {
      windLayerEnabled = !windLayerEnabled;
      const button = document.getElementById("windButton");
      button.classList.toggle("active", windLayerEnabled);
      button.setAttribute("aria-pressed", String(windLayerEnabled));
      if (windLayerEnabled) {
        windLayer.addTo(map);
        setStatus("Loading estimated 5 m wind field...");
        await refreshWindField();
      } else {
        if (windFieldRequest) windFieldRequest.abort();
        clearTimeout(windFieldTimer);
        map.removeLayer(windLayer);
        document.getElementById("windFieldSpeed").textContent = "Animation off";
        document.getElementById("windFieldBeaufort").textContent = "n/a";
        setStatus("Wind animation off.");
      }
    });
    document.getElementById("clearButton").addEventListener("click", () => {
      if (selectedMarker) selectedMarker.remove();
      if (pointerRequest) pointerRequest.abort();
      clearTimeout(pointerTimer);
      lastPointerContainerPoint = null;
      selectedMarker = null;
      measurePoints = [];
      measureLayer.clearLayers();
      document.getElementById("pointerDepth").textContent = "Move over water";
      document.getElementById("pointerPosition").textContent = "n/a";
      document.getElementById("distanceValue").textContent = "No measurement";
      document.getElementById("results").innerHTML = '<div class="metric wide"><span class="label">Status</span><span class="value">Click the map</span></div>';
      setTool("inspect");
      setStatus("Selection cleared.");
    });
  </script>
</body>
</html>
"""


def current_index(times):
    if not times:
        return None

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    parsed = [datetime.fromisoformat(value) for value in times]
    return min(range(len(parsed)), key=lambda idx: abs((parsed[idx] - now).total_seconds()))


def indexed_value(block, key, index):
    values = block.get(key) or []
    if index is None or index >= len(values):
        return None
    return values[index]


def wind_speed_at_5m(speed_10m):
    """Estimate 5 m wind from the API's standard 10 m wind value."""
    if speed_10m is None:
        return None
    try:
        speed_10m = float(speed_10m)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(speed_10m):
        return None
    # Neutral 1/7 power-law adjustment; the API does not expose a 5 m field.
    return speed_10m * (5.0 / 10.0) ** 0.143


def wind_speed_to_beaufort(speed_kmh):
    if speed_kmh is None or not math.isfinite(float(speed_kmh)):
        return None, None
    thresholds = [1, 5, 11, 19, 28, 38, 49, 61, 74, 88, 102, 117]
    descriptions = [
        "calm",
        "light air",
        "light breeze",
        "gentle breeze",
        "moderate breeze",
        "fresh breeze",
        "strong breeze",
        "near gale",
        "gale",
        "strong gale",
        "storm",
        "violent storm",
        "hurricane force",
    ]
    force = next((index for index, threshold in enumerate(thresholds) if speed_kmh <= threshold), 12)
    return force, descriptions[force]


def wind_direction_cardinal(degrees):
    if degrees is None:
        return None
    try:
        degrees = float(degrees)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(degrees):
        return None
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((degrees + 22.5) // 45) % 8]


def classify_depth(value, bathymetry_configured=False):
    if value is None:
        if bathymetry_configured:
            return None, "no sampled depth"
        return None, "not configured"
    if not math.isfinite(value) or value <= MIN_RELIABLE_DEPTH_M:
        return None, "shore / no reliable depth"
    label = f"{value:.1f} m" if value < 10 else f"{value:.0f} m"
    return value, label


def haversine_m(lat1, lon1, lat2, lon2):
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def raster_bounds(dataset):
    transform = dataset.transform
    left = transform.c
    top = transform.f
    right = transform.c + transform.a * dataset.width
    bottom = transform.f + transform.e * dataset.height
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def query_marine_point(lat, lon, timezone):
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "forecast_days": 2,
        "hourly": ",".join(
            [
                "wave_height",
                "sea_level_height_msl",
                "sea_surface_temperature",
                "ocean_current_velocity",
                "ocean_current_direction",
            ]
        ),
    }

    response = requests.get(MARINE_API_URL, params=params, timeout=(3, 8))
    response.raise_for_status()
    payload = response.json()
    hourly = payload.get("hourly", {})
    index = current_index(hourly.get("time", []))

    return {
        "model_time": indexed_value(hourly, "time", index),
        "wave_height_m": indexed_value(hourly, "wave_height", index),
        "sea_level_height_msl_m": indexed_value(hourly, "sea_level_height_msl", index),
        "sea_surface_temperature_c": indexed_value(hourly, "sea_surface_temperature", index),
        "ocean_current_velocity_kmh": indexed_value(hourly, "ocean_current_velocity", index),
        "ocean_current_direction_deg": indexed_value(hourly, "ocean_current_direction", index),
    }


def query_weather_point(lat, lon, timezone):
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "current": "wind_speed_10m,wind_direction_10m",
        "hourly": "wind_speed_10m,wind_direction_10m",
        "forecast_days": 1,
        "wind_speed_unit": "kmh",
    }

    response = requests.get(WEATHER_API_URL, params=params, timeout=(3, 8))
    response.raise_for_status()
    payload = response.json()
    current = payload.get("current") or {}
    hourly = payload.get("hourly") or {}
    hourly_index = current_index(hourly.get("time", []))
    speed_10m = current.get("wind_speed_10m")
    if speed_10m is None:
        speed_10m = indexed_value(hourly, "wind_speed_10m", hourly_index)
    speed_5m = wind_speed_at_5m(speed_10m)
    beaufort_force, beaufort_description = wind_speed_to_beaufort(speed_5m)
    direction = current.get("wind_direction_10m")
    if direction is None:
        direction = indexed_value(hourly, "wind_direction_10m", hourly_index)
    direction_cardinal = wind_direction_cardinal(direction)
    return {
        "weather_model_time": current.get("time") or indexed_value(hourly, "time", hourly_index),
        "wind_speed_10m_kmh": speed_10m,
        "wind_speed_5m_kmh": speed_5m,
        "wind_direction_5m_deg": direction,
        "wind_direction_10m_deg": direction,
        "wind_direction_cardinal": direction_cardinal,
        "beaufort_force": beaufort_force,
        "beaufort_description": beaufort_description,
        "beaufort_label": (
            f"{beaufort_force} Bf {direction_cardinal}"
            if beaufort_force is not None and direction_cardinal
            else None
        ),
    }


def query_wind_field(west, south, east, north, columns=12, rows=8):
    """Fetch a small current wind grid for the animated map layer."""
    column_denominator = max(columns - 1, 1)
    row_denominator = max(rows - 1, 1)
    longitudes = [
        west + (east - west) * index / column_denominator for index in range(columns)
    ]
    latitudes = [
        south + (north - south) * index / row_denominator for index in range(rows)
    ]
    coordinates = [(lat, lon) for lat in latitudes for lon in longitudes]
    params = {
        "latitude": ",".join(f"{lat:.5f}" for lat, _ in coordinates),
        "longitude": ",".join(f"{lon:.5f}" for _, lon in coordinates),
        "current": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kmh",
        "timezone": "UTC",
    }
    response = requests.get(WEATHER_API_URL, params=params, timeout=(4, 15))
    response.raise_for_status()
    payload = response.json()
    records = payload if isinstance(payload, list) else [payload]
    points = []
    for index, (lat, lon) in enumerate(coordinates):
        record = records[index] if index < len(records) else {}
        current = record.get("current") or {}
        speed_10m = current.get("wind_speed_10m")
        direction = current.get("wind_direction_10m")
        speed_5m = wind_speed_at_5m(speed_10m)
        if speed_5m is None or direction is None:
            points.append(None)
            continue
        direction = float(direction)
        # Meteorological direction is where wind comes from; vectors point to.
        direction_radians = math.radians(direction)
        eastward = -speed_5m * math.sin(direction_radians)
        northward = -speed_5m * math.cos(direction_radians)
        force, description = wind_speed_to_beaufort(speed_5m)
        direction_cardinal = wind_direction_cardinal(direction)
        points.append(
            {
                "lat": lat,
                "lon": lon,
                "speed_5m_kmh": speed_5m,
                "direction_5m_deg": direction,
                "direction_cardinal": direction_cardinal,
                "beaufort_force": force,
                "beaufort_description": description,
                "beaufort_label": (
                    f"{force} Bf {direction_cardinal}"
                    if force is not None and direction_cardinal
                    else None
                ),
                "eastward_kmh": eastward,
                "northward_kmh": northward,
            }
        )

    current_time = None
    if records:
        current_time = (records[0].get("current") or {}).get("time")
    representative = next((point for point in points if point), None)
    return {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "columns": columns,
        "rows": rows,
        "time": current_time,
        "points": points,
        "representative": representative,
    }


def query_emodnet_depth(lat, lon):
    """Query EMODnet's point service when no local raster covers the point."""
    response = requests.get(
        EMODNET_DEPTH_API_URL,
        params={"geom": f"POINT({lon:.8f} {lat:.8f})"},
        timeout=(3, 6),
    )
    response.raise_for_status()
    payload = response.json()
    for key in ("avg", "smoothed", "mean", "depth"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > MIN_RELIABLE_DEPTH_M:
            return value
    return None


def query_emodnet_contours(west, south, east, north, count=1500):
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typenames": "emodnet:contours",
        "bbox": f"{west},{south},{east},{north},EPSG:4326",
        "outputFormat": "application/json",
        "count": count,
    }
    response = requests.get(EMODNET_WFS_URL, params=params, timeout=(4, 14))
    response.raise_for_status()
    contours = []
    for feature in response.json().get("features", []):
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        depth = properties.get("elevation")
        if depth is None:
            continue
        if geometry.get("type") == "LineString":
            contours.append(
                {
                    "depth_m": abs(float(depth)),
                    "line": [[lat, lon] for lon, lat in geometry.get("coordinates", [])],
                }
            )
        elif geometry.get("type") == "MultiLineString":
            for line in geometry.get("coordinates", []):
                contours.append(
                    {
                        "depth_m": abs(float(depth)),
                        "line": [[lat, lon] for lon, lat in line],
                    }
                )
    return contours


def contour_levels(min_depth, max_depth):
    levels = [2, 5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 750, 1000]
    return [level for level in levels if min_depth <= level <= max_depth]


def query_high_res_contours(west, south, east, north, max_points=220000):
    hr_path = app.config.get("HIGH_RES_BATHYMETRY_PATH")
    if not hr_path:
        return []

    import contourpy
    import numpy as np

    reader = high_res_bathymetry_reader(hr_path)
    contours = []

    for grid in getattr(reader, "grids", []):
        lon_min, lat_min, lon_max, lat_max = grid["bounds"]
        if lon_max < west or lon_min > east or lat_max < south or lat_min > north:
            continue

        lon_values = grid["lon"]
        lat_values = grid["lat"]
        lon_indexes = np.where((lon_values >= west) & (lon_values <= east))[0]
        lat_indexes = np.where((lat_values >= south) & (lat_values <= north))[0]
        if len(lon_indexes) < 2 or len(lat_indexes) < 2:
            continue

        if len(lon_indexes) * len(lat_indexes) > max_points:
            step = math.ceil(math.sqrt((len(lon_indexes) * len(lat_indexes)) / max_points))
            lon_indexes = lon_indexes[::step]
            lat_indexes = lat_indexes[::step]

        lon_subset = np.asarray(lon_values[lon_indexes], dtype=float)
        lat_subset = np.asarray(lat_values[lat_indexes], dtype=float)
        lon_step = max(1, abs(int(lon_indexes[1] - lon_indexes[0])))
        lat_step = max(1, abs(int(lat_indexes[1] - lat_indexes[0])))
        elevation = np.ma.asarray(
            grid["elevation"][
                int(lat_indexes.min()) : int(lat_indexes.max()) + 1 : lat_step,
                int(lon_indexes.min()) : int(lon_indexes.max()) + 1 : lon_step,
            ],
            dtype=float,
        ).filled(np.nan)
        if lon_subset[0] > lon_subset[-1]:
            lon_subset = lon_subset[::-1]
            elevation = elevation[:, ::-1]
        if lat_subset[0] > lat_subset[-1]:
            lat_subset = lat_subset[::-1]
            elevation = elevation[::-1, :]
        depth = np.where(elevation < -MIN_RELIABLE_DEPTH_M, -elevation, np.nan)
        finite = depth[np.isfinite(depth)]
        if finite.size == 0:
            continue

        levels = contour_levels(float(finite.min()), float(finite.max()))
        if not levels:
            continue

        generator = contourpy.contour_generator(x=lon_subset, y=lat_subset, z=depth)
        label = os.path.basename(grid["path"]).replace(".nc", "")
        for level in levels:
            for line in generator.lines(level):
                if len(line) < 2:
                    continue
                contours.append(
                    {
                        "source": "hr",
                        "label": label,
                        "depth_m": float(level),
                        "line": [[float(lat), float(lon)] for lon, lat in line],
                    }
                )

    return contours


@lru_cache(maxsize=1)
def bathymetry_reader(path):
    if not path:
        return None

    if os.path.isdir(path):
        import numpy as np
        import rasterio
        from rasterio.windows import Window

        tile_pattern = re.compile(r"_r(?P<row>\d+)_c(?P<col>\d+)\.tiff?$", re.IGNORECASE)
        raster_paths = [
            os.path.join(path, name)
            for name in sorted(os.listdir(path))
            if name.lower().endswith((".tif", ".tiff"))
        ]
        grid_cols = 0
        grid_rows = 0
        for raster_path in raster_paths:
            match = tile_pattern.search(os.path.basename(raster_path))
            if match:
                grid_rows = max(grid_rows, int(match.group("row")))
                grid_cols = max(grid_cols, int(match.group("col")))

        datasets = []
        for raster_path in raster_paths:
            dataset = rasterio.open(raster_path)
            bounds = raster_bounds(dataset)
            datasets.append((dataset, bounds))

        def read(lon, lat):
            for dataset, bounds in datasets:
                left, bottom, right, top = bounds or dataset.bounds
                if not (left <= lon <= right and bottom <= lat <= top):
                    continue
                transform = dataset.transform
                col = math.floor((lon - transform.c) / transform.a)
                row = math.floor((lat - transform.f) / transform.e)
                col = max(0, min(dataset.width - 1, col))
                row = max(0, min(dataset.height - 1, row))
                sample = dataset.read(1, window=Window(col, row, 1, 1), masked=True)
                if np.ma.is_masked(sample[0, 0]):
                    return None
                value = float(sample[0, 0])
                if not math.isfinite(value) or value >= -MIN_RELIABLE_DEPTH_M:
                    return None
                return abs(value)
            return None

        return read

    ext = os.path.splitext(path)[1].lower()
    if ext in {".tif", ".tiff"}:
        import numpy as np
        import rasterio
        from rasterio.windows import Window

        dataset = rasterio.open(path)

        def read(lon, lat):
            transform = dataset.transform
            col = math.floor((lon - transform.c) / transform.a)
            row = math.floor((lat - transform.f) / transform.e)
            if not (0 <= col < dataset.width and 0 <= row < dataset.height):
                return None
            sample = dataset.read(1, window=Window(col, row, 1, 1), masked=True)
            if np.ma.is_masked(sample[0, 0]):
                return None
            value = float(sample[0, 0])
            if not math.isfinite(value) or value >= -MIN_RELIABLE_DEPTH_M:
                return None
            return abs(value)

        return read

    if ext in {".nc", ".netcdf"}:
        import numpy as np
        from netCDF4 import Dataset

        dataset = Dataset(path)
        longitude_name = next(
            (name for name in ["lon", "longitude"] if name in dataset.variables), None
        )
        latitude_name = next(
            (name for name in ["lat", "latitude"] if name in dataset.variables), None
        )
        depth_name = next(
            (name for name in ["elevation", "z", "depth", "Band1"] if name in dataset.variables),
            None,
        )
        if longitude_name is None or latitude_name is None or depth_name is None:
            raise RuntimeError("Could not find latitude, longitude, and depth variables in the NetCDF file.")
        longitude_values = np.asarray(dataset.variables[longitude_name][:], dtype=float)
        latitude_values = np.asarray(dataset.variables[latitude_name][:], dtype=float)
        depth_variable = dataset.variables[depth_name]
        if longitude_values.ndim != 1 or latitude_values.ndim != 1:
            raise RuntimeError("NetCDF coordinates must be one-dimensional.")
        try:
            latitude_axis = depth_variable.dimensions.index(latitude_name)
            longitude_axis = depth_variable.dimensions.index(longitude_name)
        except ValueError as exc:
            raise RuntimeError("NetCDF depth variable does not use its latitude/longitude coordinates.") from exc

        def read(lon, lat):
            latitude_index = int(np.abs(latitude_values - lat).argmin())
            longitude_index = int(np.abs(longitude_values - lon).argmin())
            indexer = [slice(None)] * depth_variable.ndim
            indexer[latitude_axis] = latitude_index
            indexer[longitude_axis] = longitude_index
            value = np.ma.filled(depth_variable[tuple(indexer)], np.nan)
            value = float(value)
            if not math.isfinite(value):
                return None
            if value >= -MIN_RELIABLE_DEPTH_M:
                return None
            return abs(value)

        return read

    raise RuntimeError("Bathymetry file must be a GeoTIFF or NetCDF file.")


@lru_cache(maxsize=1)
def high_res_bathymetry_reader(path):
    if not path:
        return None

    from netCDF4 import Dataset

    nc_paths = []
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for name in files:
                if name.lower().endswith(".nc"):
                    nc_paths.append(os.path.join(root, name))
    elif path.lower().endswith(".nc"):
        nc_paths.append(path)

    grids = []
    for nc_path in sorted(nc_paths):
        dataset = Dataset(nc_path)
        lon_values = dataset.variables["lon"][:]
        lat_values = dataset.variables["lat"][:]
        elevation = dataset.variables["elevation"]
        lon_min = float(min(lon_values[0], lon_values[-1]))
        lon_max = float(max(lon_values[0], lon_values[-1]))
        lat_min = float(min(lat_values[0], lat_values[-1]))
        lat_max = float(max(lat_values[0], lat_values[-1]))
        grids.append(
            {
                "path": nc_path,
                "dataset": dataset,
                "lon": lon_values,
                "lat": lat_values,
                "elevation": elevation,
                "bounds": (lon_min, lat_min, lon_max, lat_max),
            }
        )

    def read(lon, lat):
        for grid in grids:
            lon_min, lat_min, lon_max, lat_max = grid["bounds"]
            if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
                continue

            lon_values = grid["lon"]
            lat_values = grid["lat"]
            lon_index = int(abs(lon_values - lon).argmin())
            lat_index = int(abs(lat_values - lat).argmin())
            value = float(grid["elevation"][lat_index, lon_index])
            if not math.isfinite(value) or value >= -MIN_RELIABLE_DEPTH_M:
                return None, None
            return abs(value), os.path.basename(grid["path"])
        return None, None

    read.grids = grids
    return read


def query_depth_info(lat, lon, allow_remote=True):
    hr_path = app.config.get("HIGH_RES_BATHYMETRY_PATH")
    if hr_path:
        reader = high_res_bathymetry_reader(hr_path)
        if reader:
            depth, source = reader(lon, lat)
            if depth is not None:
                return depth, f"EMODnet HR {source}"

    regional_path = app.config.get("REGIONAL_BATHYMETRY_PATH")
    if regional_path:
        reader = bathymetry_reader(regional_path)
        depth = reader(lon, lat)
        if depth is not None:
            return depth, "EMODnet DTM 2024 Athens/Saronic"

    path = app.config.get("BATHYMETRY_PATH")
    if path:
        reader = bathymetry_reader(path)
        depth = reader(lon, lat)
        if depth is not None:
            return depth, "EMODnet DTM 2024"

    gebco_path = app.config.get("GEBCO_BATHYMETRY_PATH")
    if gebco_path:
        reader = bathymetry_reader(gebco_path)
        depth = reader(lon, lat)
        if depth is not None:
            return depth, "GEBCO 2026"

    west, south, east, north = EMODNET_POINT_BOUNDS
    in_emodnet_coverage = west <= lon <= east and south <= lat <= north
    if allow_remote and app.config.get("REMOTE_DEPTH_ENABLED", True) and in_emodnet_coverage:
        try:
            depth = query_emodnet_depth(lat, lon)
        except requests.RequestException:
            depth = None
        if depth is not None:
            return depth, "EMODnet point service"

    return None, None


def query_depth(lat, lon, allow_remote=True):
    depth, _ = query_depth_info(lat, lon, allow_remote=allow_remote)
    return depth


def empty_marine():
    return {
        "model_time": None,
        "wave_height_m": None,
        "sea_level_height_msl_m": None,
        "sea_surface_temperature_c": None,
        "ocean_current_velocity_kmh": None,
        "ocean_current_direction_deg": None,
    }


def empty_weather():
    return {
        "weather_model_time": None,
        "wind_speed_10m_kmh": None,
        "wind_speed_5m_kmh": None,
        "wind_direction_5m_deg": None,
        "wind_direction_10m_deg": None,
        "wind_direction_cardinal": None,
        "beaufort_force": None,
        "beaufort_description": None,
        "beaufort_label": None,
    }


def colorize_depth(depth):
    if not math.isfinite(depth) or depth <= MIN_RELIABLE_DEPTH_M:
        return 0, 0, 0, 0

    stops = [
        (1.0, (185, 241, 255)),
        (10.0, (96, 202, 229)),
        (50.0, (35, 130, 196)),
        (150.0, (23, 63, 134)),
        (500.0, (9, 15, 63)),
    ]
    capped = min(depth, stops[-1][0])
    for index in range(1, len(stops)):
        low_depth, low_color = stops[index - 1]
        high_depth, high_color = stops[index]
        if capped <= high_depth:
            ratio = (capped - low_depth) / (high_depth - low_depth)
            color = tuple(
                round(low_color[channel] + ratio * (high_color[channel] - low_color[channel]))
                for channel in range(3)
            )
            return *color, 180
    return *stops[-1][1], 180


@lru_cache(maxsize=4)
def bathymetry_tile_overlay(path, max_pixels=700):
    import numpy as np
    from PIL import Image

    if os.path.splitext(path)[1].lower() in {".nc", ".netcdf"}:
        from netCDF4 import Dataset

        with Dataset(path) as dataset:
            longitude_name = next(
                (name for name in ["lon", "longitude"] if name in dataset.variables), None
            )
            latitude_name = next(
                (name for name in ["lat", "latitude"] if name in dataset.variables), None
            )
            depth_name = next(
                (name for name in ["elevation", "z", "depth", "Band1"] if name in dataset.variables),
                None,
            )
            if longitude_name is None or latitude_name is None or depth_name is None:
                raise RuntimeError("Could not find latitude, longitude, and depth variables in the NetCDF file.")
            longitude_values = np.asarray(dataset.variables[longitude_name][:], dtype=float)
            latitude_values = np.asarray(dataset.variables[latitude_name][:], dtype=float)
            depth_variable = dataset.variables[depth_name]
            try:
                latitude_axis = depth_variable.dimensions.index(latitude_name)
                longitude_axis = depth_variable.dimensions.index(longitude_name)
            except ValueError as exc:
                raise RuntimeError("NetCDF depth variable does not use its latitude/longitude coordinates.") from exc
            if depth_variable.ndim != 2:
                raise RuntimeError("NetCDF depth variable must be two-dimensional.")
            array = np.ma.asarray(depth_variable[:], dtype=float).filled(np.nan)
            array = np.moveaxis(array, [latitude_axis, longitude_axis], [0, 1])
            if latitude_values[0] < latitude_values[-1]:
                array = array[::-1, :]
            if longitude_values[0] > longitude_values[-1]:
                array = array[:, ::-1]
            left, right = float(longitude_values.min()), float(longitude_values.max())
            bottom, top = float(latitude_values.min()), float(latitude_values.max())
            stride = max(1, math.ceil(max(array.shape) / max_pixels))
            array = array[::stride, ::stride]
        signed = array.astype("float32")
        depth = np.where(signed < -MIN_RELIABLE_DEPTH_M, -signed, 0)
        depth[~np.isfinite(depth)] = 0
    else:
        import rasterio

        with rasterio.open(path) as dataset:
            left, bottom, right, top = raster_bounds(dataset)
            scale = max(dataset.width / max_pixels, dataset.height / max_pixels, 1)
            width = max(1, round(dataset.width / scale))
            height = max(1, round(dataset.height / scale))
            array = dataset.read(1, out_shape=(height, width))
            signed = array.astype("float32")
            depth = np.where(signed < -MIN_RELIABLE_DEPTH_M, -signed, 0)
            depth[~np.isfinite(depth)] = 0
            if dataset.nodata is not None:
                depth[array == dataset.nodata] = 0

    lut = np.array([colorize_depth(float(value)) for value in range(501)], dtype=np.uint8)
    indexes = np.clip(depth, 0, 500).astype(np.uint16)
    rgba = lut[indexes]
    rgba[depth > 500] = lut[500]

    output_name = os.path.splitext(os.path.basename(path))[0] + "_bathymetry_overlay.png"
    output_path = os.path.join(tempfile.gettempdir(), output_name)
    Image.fromarray(rgba, mode="RGBA").save(output_path, optimize=True)
    return output_path, (left, bottom, right, top)


@lru_cache(maxsize=4)
def bathymetry_overlay_tiles(path):
    if not path or not os.path.isdir(path):
        return None
    raster_paths = [
        os.path.join(path, name)
        for name in sorted(os.listdir(path))
        if name.lower().endswith((".tif", ".tiff"))
    ]
    tiles = []
    for raster_path in raster_paths:
        output_path, bounds = bathymetry_tile_overlay(raster_path)
        left, bottom, right, top = bounds
        tiles.append(
            {
                "id": os.path.splitext(os.path.basename(raster_path))[0],
                "path": output_path,
                "west": left,
                "south": bottom,
                "east": right,
                "north": top,
            }
        )
    return tiles


def bathymetry_overlay_entries():
    entries = list(bathymetry_overlay_tiles(app.config.get("BATHYMETRY_PATH")) or [])
    regional_path = app.config.get("REGIONAL_BATHYMETRY_PATH")
    if regional_path and os.path.isfile(regional_path):
        output_path, bounds = bathymetry_tile_overlay(regional_path)
        left, bottom, right, top = bounds
        entries.append(
            {
                "id": os.path.splitext(os.path.basename(regional_path))[0],
                "path": output_path,
                "west": left,
                "south": bottom,
                "east": right,
                "north": top,
            }
        )
    return entries


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/bathymetry-overlays")
def bathymetry_overlays():
    tiles = bathymetry_overlay_entries()
    if not tiles:
        return jsonify({"error": "No local bathymetry overlays configured."}), 404
    return jsonify(
        {
            "tiles": [
                {
                    "id": tile["id"],
                    "url": f"/api/bathymetry-overlay/{tile['id']}",
                    "west": tile["west"],
                    "south": tile["south"],
                    "east": tile["east"],
                    "north": tile["north"],
                }
                for tile in tiles
            ],
            "source": "Local EMODnet DTM tiles and regional detail",
        }
    )


@app.get("/api/bathymetry-overlay/<tile_id>")
def bathymetry_overlay(tile_id):
    tiles = bathymetry_overlay_entries()
    if not tiles:
        return jsonify({"error": "No local bathymetry overlays configured."}), 404
    for tile in tiles:
        if tile["id"] == tile_id:
            return send_file(tile["path"], mimetype="image/png", max_age=0)
    return jsonify({"error": "Unknown bathymetry tile."}), 404


@app.get("/api/hr-depth-contours")
@app.get("/api/depth-contours")
def depth_contours():
    try:
        west = float(request.args["west"])
        south = float(request.args["south"])
        east = float(request.args["east"])
        north = float(request.args["north"])
    except (KeyError, ValueError):
        return jsonify({"error": "Provide west, south, east, and north query parameters."}), 400

    west_limit, south_limit, east_limit, north_limit = WORLD_BOUNDS
    west = max(west, west_limit)
    south = max(south, south_limit)
    east = min(east, east_limit)
    north = min(north, north_limit)

    if not all(math.isfinite(value) for value in (west, south, east, north)):
        return jsonify({"error": "Contour bounds must be finite numbers."}), 400
    if west >= east or south >= north:
        return jsonify({"contours": []})
    if (east - west) * (north - south) > 3:
        return jsonify({"contours": []})

    include_hr = request.args.get("hr", "1") != "0"
    include_coarse = request.args.get("coarse", "1") != "0"

    try:
        contours = []
        if include_hr:
            contours.extend(query_high_res_contours(west, south, east, north))
        if include_coarse:
            coarse = query_emodnet_contours(west, south, east, north)
            for feature in coarse:
                feature["source"] = "coarse"
                feature["label"] = "EMODnet contours"
            contours.extend(coarse)
        return jsonify({"contours": contours})
    except requests.RequestException as exc:
        return jsonify({"error": f"EMODnet contour request failed: {exc}"}), 502
    except Exception as exc:
        return jsonify({"error": f"Contour generation failed: {exc}"}), 500


def parse_point_coordinates():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Provide numeric lat and lon query parameters.")

    if not math.isfinite(lat) or not math.isfinite(lon):
        raise ValueError("Latitude and longitude must be finite numbers.")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError("Latitude must be between -90 and 90; longitude between -180 and 180.")
    return lat, lon


def depth_response(lat, lon, allow_remote=True):
    raw_depth, depth_source = query_depth_info(lat, lon, allow_remote=allow_remote)
    depth, depth_label = classify_depth(
        raw_depth,
        bool(
            app.config.get("BATHYMETRY_PATH")
            or app.config.get("HIGH_RES_BATHYMETRY_PATH")
            or app.config.get("REGIONAL_BATHYMETRY_PATH")
            or app.config.get("GEBCO_BATHYMETRY_PATH")
        ),
    )
    return {
        "latitude": lat,
        "longitude": lon,
        "depth_m": depth,
        "raw_depth_m": raw_depth,
        "depth_label": depth_label,
        "depth_source": depth_source,
    }


@app.get("/api/depth")
def depth():
    try:
        lat, lon = parse_point_coordinates()
        allow_remote = request.args.get("remote", "1") != "0"
        return jsonify(depth_response(lat, lon, allow_remote=allow_remote))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/point")
def point():
    try:
        lat, lon = parse_point_coordinates()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selected_depth = depth_response(lat, lon)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    marine = empty_marine()
    weather = empty_weather()
    notes = []

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = {
            executor.submit(query_marine_point, lat, lon, app.config["TIMEZONE"]): "marine",
            executor.submit(query_weather_point, lat, lon, app.config["TIMEZONE"]): "weather",
        }
        done, pending = wait(futures, timeout=9)
        for future in pending:
            notes.append(f"{futures[future]} timed out")
            future.cancel()
        for future in done:
            name = futures[future]
            try:
                result = future.result()
            except requests.RequestException:
                notes.append(f"{name} unavailable")
                continue
            except Exception:
                notes.append(f"{name} failed")
                continue

            if name == "marine":
                marine = result
            elif name == "weather":
                weather = result
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    source_note = (
        "Marine/weather forecast from Open-Meteo. Depth uses local high-resolution files "
        "when available, then the Athens/Saronic regional DTM, the Greece DTM, GEBCO 2026, "
        "and finally EMODnet's point service. Wind is estimated at 5 m from "
        "the API's standard 10 m model wind. The coloured global layer is for orientation "
        "and is not suitable for navigation."
    )
    if notes:
        source_note += " " + "; ".join(notes) + "."

    return jsonify(
        {
            **selected_depth,
            "source_note": source_note,
            **marine,
            **weather,
        }
    )


@app.get("/api/wind-field")
def wind_field():
    try:
        west = float(request.args["west"])
        south = float(request.args["south"])
        east = float(request.args["east"])
        north = float(request.args["north"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Provide west, south, east, and north query parameters."}), 400

    if not all(math.isfinite(value) for value in (west, south, east, north)):
        return jsonify({"error": "Wind-field bounds must be finite numbers."}), 400
    west = max(west, WORLD_BOUNDS[0])
    south = max(south, WORLD_BOUNDS[1])
    east = min(east, WORLD_BOUNDS[2])
    north = min(north, WORLD_BOUNDS[3])
    if west >= east or south >= north:
        return jsonify({"error": "Wind-field bounds are empty."}), 400

    try:
        return jsonify(query_wind_field(west, south, east, north))
    except requests.RequestException as exc:
        return jsonify({"error": f"Open-Meteo wind request failed: {exc}"}), 502
    except Exception as exc:
        return jsonify({"error": f"Wind-field generation failed: {exc}"}), 500


def parse_args():
    parser = argparse.ArgumentParser(
        description="Interactive world ocean bathymetry and marine conditions map."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument(
        "--bathymetry",
        default=None,
        help="Local GEBCO/EMODnet GeoTIFF, NetCDF, or tiled GeoTIFF directory.",
    )
    parser.add_argument(
        "--high-res-bathymetry",
        default=None,
        help="Optional EMODnet high-resolution NetCDF file/directory. Used before --bathymetry.",
    )
    parser.add_argument(
        "--regional-bathymetry",
        default=None,
        help="Optional regional EMODnet DTM GeoTIFF/NetCDF, used after HR files.",
    )
    parser.add_argument(
        "--gebco",
        default=None,
        help="Optional GEBCO 2026 NetCDF/GeoTIFF fallback, used after local EMODnet data.",
    )
    parser.add_argument(
        "--no-remote-depth",
        action="store_true",
        help="Do not query EMODnet's online point service outside local files.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode. The reloader stays disabled for cleaner local runs.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    app.config["BATHYMETRY_PATH"] = args.bathymetry or (
        str(DEFAULT_BATHYMETRY_PATH) if DEFAULT_BATHYMETRY_PATH.is_dir() else None
    )
    app.config["HIGH_RES_BATHYMETRY_PATH"] = args.high_res_bathymetry or (
        str(DEFAULT_HIGH_RES_BATHYMETRY_PATH)
        if DEFAULT_HIGH_RES_BATHYMETRY_PATH.is_dir()
        else None
    )
    app.config["REGIONAL_BATHYMETRY_PATH"] = args.regional_bathymetry or (
        default_regional_bathymetry_path()
    )
    app.config["GEBCO_BATHYMETRY_PATH"] = args.gebco or (
        str(DEFAULT_GEBCO_BATHYMETRY_PATH)
        if DEFAULT_GEBCO_BATHYMETRY_PATH.is_file()
        else None
    )
    app.config["TIMEZONE"] = args.timezone
    app.config["REMOTE_DEPTH_ENABLED"] = not args.no_remote_depth
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
