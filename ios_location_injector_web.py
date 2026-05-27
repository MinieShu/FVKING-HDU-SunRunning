import socket
import time
import threading
import webbrowser
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ios_location_injector import (
    AUTO_STOP_DISTANCE_METERS,
    GPS_JUMP_GUARD_SPEED_MPS,
    IOSLocationController,
    LOCATION_SETTLE_SECONDS,
    bearing_degrees,
    build_running_sway_route,
    build_loop_route,
    distance_m,
    gcj02_to_wgs84,
    keep_point_outside_restricted_areas,
    limit_route_step_distance,
    validate_running_plan,
)


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>iOS 虚拟定位注入工具</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #172033;
      --muted: #667085;
      --line: #d0d7e2;
      --blue: #2563eb;
      --red: #dc2626;
      --green: #15945f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(520px, 1fr) 300px;
      gap: 0;
    }
    .map-pane {
      padding: 18px;
      display: flex;
      flex-direction: column;
      min-width: 0;
      position: relative;
    }
    .status {
      min-height: 28px;
      color: var(--muted);
      font-size: 14px;
      display: flex;
      align-items: center;
    }
    canvas {
      width: 100%;
      flex: 1;
      min-height: 460px;
      border: 1px solid var(--line);
      background: #edf2f7;
      display: block;
    }
    .controls {
      background: var(--panel);
      border-left: 1px solid var(--line);
      padding: 18px;
      overflow: auto;
    }
    h1 {
      margin: 0 0 18px;
      font-size: 18px;
      line-height: 1.35;
    }
    label {
      display: block;
      font-size: 13px;
      color: #344054;
      margin: 12px 0 6px;
    }
    input {
      width: 100%;
      height: 34px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
    }
    button {
      width: 100%;
      height: 36px;
      border: 0;
      border-radius: 6px;
      margin-top: 10px;
      color: white;
      background: var(--blue);
      font: inherit;
      cursor: pointer;
    }
    button.secondary { background: #475569; }
    button.warn { background: var(--red); }
    button:disabled { opacity: .58; cursor: default; }
    .map-tools {
      position: absolute;
      right: 30px;
      top: 54px;
      display: flex;
      gap: 4px;
      padding: 4px;
      border: 1px solid rgba(15, 23, 42, .16);
      border-radius: 8px;
      background: rgba(255, 255, 255, .9);
      box-shadow: 0 8px 22px rgba(15, 23, 42, .12);
      z-index: 2;
    }
    .map-tools button {
      width: auto;
      height: 30px;
      margin: 0;
      padding: 0 10px;
      border-radius: 5px;
      color: #334155;
      background: transparent;
      font-size: 13px;
    }
    .map-tools button.active {
      color: white;
      background: var(--blue);
    }
    .device {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      color: var(--blue);
      background: #f8fbff;
      font-size: 14px;
      min-height: 42px;
      display: flex;
      align-items: center;
    }
    .group {
      padding-bottom: 14px;
      margin-bottom: 14px;
      border-bottom: 1px solid #e5e7eb;
    }
    .track-options {
      display: grid;
      gap: 8px;
      margin-top: 8px;
    }
    .track-card {
      display: grid;
      grid-template-columns: 18px 1fr;
      gap: 8px;
      align-items: start;
      padding: 10px;
      border: 1px solid #d8dee8;
      border-radius: 8px;
      background: #fbfdff;
      cursor: pointer;
    }
    .track-card.selected {
      border-color: var(--blue);
      background: #eef5ff;
      box-shadow: inset 0 0 0 1px rgba(37, 99, 235, .2);
    }
    .track-card input {
      width: 16px;
      height: 16px;
      margin: 2px 0 0;
      padding: 0;
    }
    .track-name {
      font-size: 14px;
      font-weight: 650;
      color: #1f2937;
      line-height: 1.25;
    }
    .track-meta {
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .toast {
      position: fixed;
      left: 18px;
      bottom: 18px;
      max-width: min(620px, calc(100vw - 36px));
      padding: 12px 14px;
      border-radius: 8px;
      background: #111827;
      color: white;
      box-shadow: 0 12px 30px rgba(15, 23, 42, .22);
      display: none;
      white-space: pre-wrap;
    }
    @media (max-width: 860px) {
      .app { grid-template-columns: 1fr; }
      .controls { border-left: 0; border-top: 1px solid var(--line); }
      canvas { min-height: 360px; }
    }
  </style>
</head>
<body>
  <main class="app">
    <section class="map-pane">
      <div id="routeStatus" class="status">在地图上点击添加跑步路线点</div>
      <div class="map-tools" aria-label="地图图层">
        <button id="satelliteLayer" class="active" type="button">卫星</button>
        <button id="standardLayer" type="button">标准</button>
      </div>
      <canvas id="map" width="900" height="620"></canvas>
    </section>
    <aside class="controls">
      <h1>iOS 虚拟定位注入工具</h1>
      <div class="group">
        <label>设备状态</label>
        <div id="deviceStatus" class="device">未连接</div>
        <button id="connect">连接/刷新设备</button>
      </div>
      <div class="group">
        <label>纬度</label>
        <input id="lat" value="39.908823" inputmode="decimal" />
        <label>经度</label>
        <input id="lon" value="116.397470" inputmode="decimal" />
        <button id="center">定位到输入坐标</button>
        <button id="inject">注入当前坐标</button>
        <button id="clear" class="warn">清除虚拟定位</button>
      </div>
      <div class="group">
        <label>预设轨道</label>
        <div id="trackOptions" class="track-options"></div>
      </div>
      <div class="group">
        <label>跑步速度 km/h</label>
        <input id="speed" value="10.0" inputmode="decimal" />
        <label>注入间隔 秒</label>
        <input id="interval" value="2.0" inputmode="decimal" />
        <label>步态摆动 米</label>
        <input id="drift" value="0.5" inputmode="decimal" />
        <label>模拟圈数</label>
        <input id="laps" value="6" inputmode="numeric" />
        <button id="startRun">开始轨迹跑步</button>
        <button id="stopRun" class="warn">停止跑步</button>
      </div>
      <button id="undo" class="secondary">撤销上一个点</button>
      <button id="clearRoute" class="secondary">清空路线</button>
    </aside>
  </main>
  <div id="toast" class="toast"></div>

  <script>
    const canvas = document.getElementById('map');
    const ctx = canvas.getContext('2d');
    const routeStatus = document.getElementById('routeStatus');
    const deviceStatus = document.getElementById('deviceStatus');
    const fields = {
      lat: document.getElementById('lat'),
      lon: document.getElementById('lon'),
      speed: document.getElementById('speed'),
      interval: document.getElementById('interval'),
      drift: document.getElementById('drift'),
      laps: document.getElementById('laps')
    };
    const PRESET_TRACKS = [
      {
        id: 'hdu_ufo',
        name: '体育馆',
        distanceMeters: 768,
        closedLoop: true,
        startPoint: {lat: 30.314193250868055, lng: 120.34040256076389},
        markingArea: {
          type: 'bbox',
          southWest: {lat: 30.31211236, lng: 120.33941541883681},
          northEast: {lat: 30.31422852, lng: 120.3411556}
        },
        restrictedAreas: []
      },
      {
        id: 'hdu_east',
        name: '东操场',
        distanceMeters: 386,
        closedLoop: true,
        startPoint: {lat: 30.31430939019097, lng: 120.34784695095487},
        markingArea: {
          type: 'bbox',
          southWest: {lat: 30.314309390190974, lng: 120.3470779079861},
          northEast: {lat: 30.315380859375, lng: 120.34784695095485}
        },
        restrictedAreas: [
          {
            type: 'polygon',
            points: [
              {lat: 30.31435031467014, lng: 120.34711588541667},
              {lat: 30.31435031467014, lng: 120.34780788845482},
              {lat: 30.315342881944446, lng: 120.34780788845482},
              {lat: 30.315342881944446, lng: 120.34711588541667}
            ]
          }
        ]
      },
      {
        id: 'hdu_living',
        name: '生活区',
        distanceMeters: 434,
        closedLoop: true,
        startPoint: {lat: 30.316688910590276, lng: 120.34056830512153},
        markingArea: {
          type: 'bbox',
          southWest: {lat: 30.31631591796875, lng: 120.33967393663194},
          northEast: {lat: 30.317822265625, lng: 120.34056830512152}
        },
        restrictedAreas: []
      }
    ];
    const GENERATION_RULES = {
      coordinateSystem: 'gcj02',
      mustStayInsideMarkingArea: true,
      mustAvoidRestrictedAreas: true,
      closedLoopRequired: true,
      routeInsetMeters: 2,
      defaultTrackId: 'hdu_east'
    };
    const state = {
      centerLat: 30.31430939019097,
      centerLon: 120.34784695095487,
      route: [],
      selectedRoutePointIndex: null,
      selectedTrackId: GENERATION_RULES.defaultTrackId,
      current: null,
      real: null,
      zoom: 19,
      minZoom: 3,
      maxZoom: 19,
      tileSize: 256,
      layer: 'satellite',
      running: false
    };
    const tileCache = new Map();
    const transformPI = Math.PI;
    const transformA = 6378245.0;
    const transformEE = 0.00669342162296594323;

    function metersPerLonDegree(lat) {
      return 111320.0 * Math.cos(lat * Math.PI / 180);
    }
    function outOfChina(lat, lon) {
      return lon < 72.004 || lon > 137.8347 || lat < 0.8293 || lat > 55.8271;
    }
    function transformLat(x, y) {
      let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y;
      ret += 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
      ret += (20.0 * Math.sin(6.0 * x * transformPI) + 20.0 * Math.sin(2.0 * x * transformPI)) * 2.0 / 3.0;
      ret += (20.0 * Math.sin(y * transformPI) + 40.0 * Math.sin(y / 3.0 * transformPI)) * 2.0 / 3.0;
      ret += (160.0 * Math.sin(y / 12.0 * transformPI) + 320.0 * Math.sin(y * transformPI / 30.0)) * 2.0 / 3.0;
      return ret;
    }
    function transformLon(x, y) {
      let ret = 300.0 + x + 2.0 * y + 0.1 * x * x;
      ret += 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
      ret += (20.0 * Math.sin(6.0 * x * transformPI) + 20.0 * Math.sin(2.0 * x * transformPI)) * 2.0 / 3.0;
      ret += (20.0 * Math.sin(x * transformPI) + 40.0 * Math.sin(x / 3.0 * transformPI)) * 2.0 / 3.0;
      ret += (150.0 * Math.sin(x / 12.0 * transformPI) + 300.0 * Math.sin(x / 30.0 * transformPI)) * 2.0 / 3.0;
      return ret;
    }
    function wgs84ToGcj02(lat, lon) {
      if (outOfChina(lat, lon)) return {lat, lon};

      let dLat = transformLat(lon - 105.0, lat - 35.0);
      let dLon = transformLon(lon - 105.0, lat - 35.0);
      const radLat = lat / 180.0 * transformPI;
      let magic = Math.sin(radLat);
      magic = 1 - transformEE * magic * magic;
      const sqrtMagic = Math.sqrt(magic);
      dLat = (dLat * 180.0) / ((transformA * (1 - transformEE)) / (magic * sqrtMagic) * transformPI);
      dLon = (dLon * 180.0) / (transformA / sqrtMagic * Math.cos(radLat) * transformPI);
      return {lat: lat + dLat, lon: lon + dLon};
    }
    function gcj02ToWgs84(lat, lon) {
      if (outOfChina(lat, lon)) return {lat, lon};
      const gcj = wgs84ToGcj02(lat, lon);
      return {lat: lat * 2 - gcj.lat, lon: lon * 2 - gcj.lon};
    }
    function normalizePoint(point) {
      return {lat: point.lat, lon: point.lon ?? point.lng};
    }
    function selectedTrack() {
      return PRESET_TRACKS.find(track => track.id === state.selectedTrackId) || PRESET_TRACKS[0];
    }
    function bboxCorners(markingArea) {
      const sw = normalizePoint(markingArea.southWest);
      const ne = normalizePoint(markingArea.northEast);
      return [
        {lat: sw.lat, lon: sw.lon},
        {lat: sw.lat, lon: ne.lon},
        {lat: ne.lat, lon: ne.lon},
        {lat: ne.lat, lon: sw.lon}
      ];
    }
    function routeBboxCorners(track) {
      const sw = normalizePoint(track.markingArea.southWest);
      const ne = normalizePoint(track.markingArea.northEast);
      const start = normalizePoint(track.startPoint);
      const meanLat = (sw.lat + ne.lat) / 2;
      const lonScale = metersPerLonDegree(meanLat);
      const inset = GENERATION_RULES.routeInsetMeters;
      const innerSw = {
        lat: sw.lat + inset / 111320.0,
        lon: sw.lon + inset / lonScale
      };
      const innerNe = {
        lat: ne.lat - inset / 111320.0,
        lon: ne.lon - inset / lonScale
      };
      const fullWidth = Math.abs(innerNe.lon - innerSw.lon) * lonScale;
      const fullHeight = Math.abs(innerNe.lat - innerSw.lat) * 111320.0;
      const targetHalfPerimeter = track.distanceMeters / 2;

      if (track.distanceMeters > 0 && targetHalfPerimeter > fullWidth && targetHalfPerimeter <= fullWidth + fullHeight) {
        const targetHeight = targetHalfPerimeter - fullWidth;
        const targetLatSpan = targetHeight / 111320.0;
        let south = start.lat - targetLatSpan / 2;
        south = Math.max(innerSw.lat, Math.min(south, innerNe.lat - targetLatSpan));
        const north = south + targetLatSpan;
        return [
          {lat: south, lon: innerSw.lon},
          {lat: south, lon: innerNe.lon},
          {lat: north, lon: innerNe.lon},
          {lat: north, lon: innerSw.lon}
        ];
      }

      if (track.distanceMeters > 0 && targetHalfPerimeter > fullHeight && targetHalfPerimeter <= fullWidth + fullHeight) {
        const targetWidth = targetHalfPerimeter - fullHeight;
        const targetLonSpan = targetWidth / lonScale;
        let west = start.lon - targetLonSpan / 2;
        west = Math.max(innerSw.lon, Math.min(west, innerNe.lon - targetLonSpan));
        const east = west + targetLonSpan;
        return [
          {lat: innerSw.lat, lon: west},
          {lat: innerSw.lat, lon: east},
          {lat: innerNe.lat, lon: east},
          {lat: innerNe.lat, lon: west}
        ];
      }

      return [
        {lat: innerSw.lat, lon: innerSw.lon},
        {lat: innerSw.lat, lon: innerNe.lon},
        {lat: innerNe.lat, lon: innerNe.lon},
        {lat: innerNe.lat, lon: innerSw.lon}
      ];
    }
    function closestEdgeStart(start, corners) {
      let best = {index: 0, point: start, distance: Infinity};
      corners.forEach((a, index) => {
        const b = corners[(index + 1) % corners.length];
        const meanLat = (a.lat + b.lat + start.lat) / 3;
        const scaleX = metersPerLonDegree(meanLat);
        const ax = a.lon * scaleX;
        const ay = a.lat * 111320.0;
        const bx = b.lon * scaleX;
        const by = b.lat * 111320.0;
        const sx = start.lon * scaleX;
        const sy = start.lat * 111320.0;
        const dx = bx - ax;
        const dy = by - ay;
        const t = Math.max(0, Math.min(1, ((sx - ax) * dx + (sy - ay) * dy) / (dx * dx + dy * dy || 1)));
        const projected = {
          lat: (ay + dy * t) / 111320.0,
          lon: (ax + dx * t) / scaleX
        };
        const edgeDistance = distance(start, projected);
        if (edgeDistance < best.distance) best = {index, point: edgeDistance <= 8 ? start : projected, distance: edgeDistance};
      });
      return best;
    }
    function buildTrackRoute(track) {
      const corners = routeBboxCorners(track);
      const start = normalizePoint(track.startPoint);
      const edge = closestEdgeStart(start, corners);
      const route = [edge.point];

      for (let offset = 1; offset <= corners.length; offset++) {
        const corner = corners[(edge.index + offset) % corners.length];
        if (distance(route[route.length - 1], corner) > 0.5) route.push(corner);
      }

      return route;
    }
    function markingAreaPolygon(track) {
      if (track.markingArea.type === 'bbox') return bboxCorners(track.markingArea);
      return (track.markingArea.points || []).map(normalizePoint);
    }
    function renderTrackOptions() {
      const container = document.getElementById('trackOptions');
      container.innerHTML = '';
      PRESET_TRACKS.forEach(track => {
        const label = document.createElement('label');
        label.className = `track-card${track.id === state.selectedTrackId ? ' selected' : ''}`;
        label.innerHTML = `
          <input type="radio" name="presetTrack" value="${track.id}" ${track.id === state.selectedTrackId ? 'checked' : ''} />
          <span>
            <span class="track-name">${track.name}</span>
            <span class="track-meta">${track.distanceMeters} 米 · 闭环</span>
          </span>
        `;
        label.querySelector('input').addEventListener('change', () => selectTrack(track.id));
        container.appendChild(label);
      });
    }
    function selectTrack(trackId) {
      state.selectedTrackId = trackId;
      const track = selectedTrack();
      const start = normalizePoint(track.startPoint);
      state.route = buildTrackRoute(track);
      state.selectedRoutePointIndex = 0;
      state.centerLat = start.lat;
      state.centerLon = start.lon;
      state.current = start;
      fields.lat.value = start.lat.toFixed(6);
      fields.lon.value = start.lon.toFixed(6);
      renderTrackOptions();
      draw();
    }
    function mapPointForLayer(lat, lon) {
      return state.layer === 'satellite' ? gcj02ToWgs84(lat, lon) : {lat, lon};
    }
    function distance(a, b) {
      const meanLat = (a.lat + b.lat) / 2;
      const dx = (b.lon - a.lon) * metersPerLonDegree(meanLat);
      const dy = (b.lat - a.lat) * 111320.0;
      return Math.hypot(dx, dy);
    }
    function worldSize() {
      return state.tileSize * Math.pow(2, state.zoom);
    }
    function latlonToWorld(lat, lon) {
      const size = worldSize();
      const sinLat = Math.sin(Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI / 180);
      return {
        x: (lon + 180) / 360 * size,
        y: (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * size
      };
    }
    function worldToLatlon(x, y) {
      const size = worldSize();
      const lon = x / size * 360 - 180;
      const n = Math.PI - 2 * Math.PI * y / size;
      const lat = 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
      return {lat, lon};
    }
    function latlonToXY(lat, lon) {
      const displayCenter = mapPointForLayer(state.centerLat, state.centerLon);
      const displayPoint = mapPointForLayer(lat, lon);
      const center = latlonToWorld(displayCenter.lat, displayCenter.lon);
      const point = latlonToWorld(displayPoint.lat, displayPoint.lon);
      return {
        x: canvas.width / 2 + point.x - center.x,
        y: canvas.height / 2 + point.y - center.y
      };
    }
    function xyToLatlon(x, y) {
      const displayCenter = mapPointForLayer(state.centerLat, state.centerLon);
      const center = latlonToWorld(displayCenter.lat, displayCenter.lon);
      const point = worldToLatlon(center.x + x - canvas.width / 2, center.y + y - canvas.height / 2);
      return state.layer === 'satellite' ? wgs84ToGcj02(point.lat, point.lon) : point;
    }
    function tileUrl(x, y, z, style) {
      if (style === 'imagery') {
        return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
      }

      const subdomain = Math.abs(x + y) % 4 + 1;
      return `https://webrd0${subdomain}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=${style}&x=${x}&y=${y}&z=${z}`;
    }
    function maxZoomForLayer() {
      return state.layer === 'standard' ? 18 : state.maxZoom;
    }
    function drawTileLayer(style, fillMissing) {
      const displayCenter = style === 'imagery' ? gcj02ToWgs84(state.centerLat, state.centerLon) : {
        lat: state.centerLat,
        lon: state.centerLon
      };
      const center = latlonToWorld(displayCenter.lat, displayCenter.lon);
      const topLeft = {
        x: center.x - canvas.width / 2,
        y: center.y - canvas.height / 2
      };
      const startX = Math.floor(topLeft.x / state.tileSize);
      const startY = Math.floor(topLeft.y / state.tileSize);
      const endX = Math.floor((topLeft.x + canvas.width) / state.tileSize);
      const endY = Math.floor((topLeft.y + canvas.height) / state.tileSize);
      const maxTile = Math.pow(2, state.zoom);
      let loading = false;

      for (let tx = startX; tx <= endX; tx++) {
        for (let ty = startY; ty <= endY; ty++) {
          if (ty < 0 || ty >= maxTile) continue;
          const wrappedX = ((tx % maxTile) + maxTile) % maxTile;
          const key = `${style}/${state.zoom}/${wrappedX}/${ty}`;
          const dx = Math.round(tx * state.tileSize - topLeft.x);
          const dy = Math.round(ty * state.tileSize - topLeft.y);
          let tile = tileCache.get(key);
          if (!tile) {
            tile = new Image();
            tile.onload = () => draw();
            tile.onerror = () => draw();
            tile.src = tileUrl(wrappedX, ty, state.zoom, style);
            tileCache.set(key, tile);
          }
          if (tile.complete && tile.naturalWidth > 0) {
            ctx.drawImage(tile, dx, dy, state.tileSize, state.tileSize);
          } else {
            loading = true;
            if (fillMissing) {
              ctx.fillStyle = '#edf2f7';
              ctx.fillRect(dx, dy, state.tileSize, state.tileSize);
            }
          }
        }
      }
      return loading;
    }
    function drawMapTiles() {
      ctx.fillStyle = '#dce6ef';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      const styles = state.layer === 'satellite' ? ['imagery'] : [7];
      let loading = false;
      styles.forEach((style, index) => {
        loading = drawTileLayer(style, index === 0) || loading;
      });

      if (loading) {
        ctx.fillStyle = 'rgba(17, 24, 39, .74)';
        ctx.fillRect(12, 10, 120, 28);
        ctx.fillStyle = '#fff';
        ctx.font = '13px -apple-system, BlinkMacSystemFont, sans-serif';
        ctx.fillText('正在加载地图...', 24, 29);
      }
    }
    function updateRouteStatus(text) {
      if (text) {
        routeStatus.textContent = text;
        return;
      }
      if (!state.route.length) {
        routeStatus.textContent = '在地图上点击添加跑步路线点';
        return;
      }
      const closeIndex = closePointIndex();
      const lap = state.route.slice(closeIndex).concat(state.route.slice(0, closeIndex + 1));
      let total = 0;
      for (let i = 1; i < lap.length; i++) total += distance(lap[i - 1], lap[i]);
      const track = selectedTrack();
      routeStatus.textContent = `已选择：${track.name}  目标：${track.distanceMeters} 米  单圈约：${total.toFixed(0)} 米`;
    }
    function closePointIndex() {
      if (state.route.length < 2) return 0;
      if (state.selectedRoutePointIndex === null) return 0;
      return Math.max(0, Math.min(state.selectedRoutePointIndex, state.route.length - 1));
    }
    function hitRoutePoint(x, y) {
      let nearestIndex = null;
      let nearestDistance = 14;
      state.route.forEach((p, index) => {
        const pt = latlonToXY(p.lat, p.lon);
        const pointDistance = Math.hypot(pt.x - x, pt.y - y);
        if (pointDistance <= nearestDistance) {
          nearestIndex = index;
          nearestDistance = pointDistance;
        }
      });
      return nearestIndex;
    }
    function draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawMapTiles();
      ctx.strokeStyle = '#718096';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(canvas.width / 2 - 10, canvas.height / 2);
      ctx.lineTo(canvas.width / 2 + 10, canvas.height / 2);
      ctx.moveTo(canvas.width / 2, canvas.height / 2 - 10);
      ctx.lineTo(canvas.width / 2, canvas.height / 2 + 10);
      ctx.stroke();
      ctx.fillStyle = '#2d3748';
      ctx.font = '14px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.fillText('点击添加路线点，点击已有点设为闭合点，拖拽移动地图，滚轮缩放', 12, canvas.height - 48);
      const scaleX = canvas.width - 140;
      const scaleY = canvas.height - 28;
      ctx.strokeStyle = '#2d3748';
      ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(scaleX, scaleY); ctx.lineTo(scaleX + 100, scaleY); ctx.stroke();
      const metersPerPixel = Math.cos(state.centerLat * Math.PI / 180) * 156543.03392 / Math.pow(2, state.zoom);
      ctx.fillText(`${Math.round(metersPerPixel * 100)} 米`, scaleX + 28, scaleY - 10);

      drawPresetAreas();

      if (state.route.length >= 2) {
        ctx.strokeStyle = '#e05a47';
        ctx.lineWidth = 3;
        ctx.beginPath();
        state.route.forEach((p, i) => {
          const pt = latlonToXY(p.lat, p.lon);
          if (i === 0) ctx.moveTo(pt.x, pt.y); else ctx.lineTo(pt.x, pt.y);
        });
        ctx.stroke();

        const first = latlonToXY(state.route[0].lat, state.route[0].lon);
        const last = latlonToXY(state.route[state.route.length - 1].lat, state.route[state.route.length - 1].lon);
        ctx.save();
        ctx.strokeStyle = '#16a34a';
        ctx.lineWidth = 3;
        ctx.setLineDash([7, 5]);
        ctx.beginPath();
        ctx.moveTo(last.x, last.y);
        ctx.lineTo(first.x, first.y);
        ctx.stroke();
        ctx.restore();
      }
      state.route.forEach((p, index) => {
        const pt = latlonToXY(p.lat, p.lon);
        const selected = state.selectedRoutePointIndex === index;
        const radius = selected ? 12 : 7;
        ctx.fillStyle = index === 0 ? '#2b6cb0' : '#dd6b20';
        ctx.strokeStyle = selected ? '#22c55e' : 'white';
        ctx.lineWidth = selected ? 3 : 2;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#1a202c';
        ctx.font = 'bold 13px Arial';
        ctx.textAlign = 'center';
        ctx.fillText(String(index + 1), pt.x, pt.y - radius - 8);
        ctx.textAlign = 'left';
      });
      if (state.real) {
        const pt = latlonToXY(state.real.lat, state.real.lon);
        ctx.save();
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 10, 0, Math.PI * 2); ctx.stroke();
        ctx.restore();
        ctx.fillStyle = '#1d4ed8';
        ctx.font = '14px Arial';
        ctx.fillText('真实位置', pt.x + 14, pt.y + 4);
      }
      if (state.current) {
        const pt = latlonToXY(state.current.lat, state.current.lon);
        drawRunnerMarker(pt.x, pt.y, state.current.heading ?? 0);
        ctx.fillStyle = '#0f5132';
        ctx.font = '14px Arial';
        ctx.fillText('虚拟定位', pt.x + 14, pt.y + 4);
      }
      updateRouteStatus();
    }
    function rotatedPoint(x, y, angle) {
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      return {x: x * cos - y * sin, y: x * sin + y * cos};
    }
    function markerPoint(centerX, centerY, x, y, angle) {
      const point = rotatedPoint(x, y, angle);
      return {x: centerX + point.x, y: centerY + point.y};
    }
    function drawRunnerMarker(x, y, headingDegrees) {
      const angle = headingDegrees * Math.PI / 180;
      const body = [[-6, 5], [0, -12], [6, 5], [0, 10]].map(([px, py]) => markerPoint(x, y, px, py, angle));
      ctx.save();
      ctx.fillStyle = '#15a46b';
      ctx.strokeStyle = 'white';
      ctx.lineWidth = 2;
      ctx.beginPath();
      body.forEach((point, index) => {
        if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y);
      });
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      const head = markerPoint(x, y, 0, -18, angle);
      ctx.fillStyle = '#0f5132';
      ctx.beginPath();
      ctx.arc(head.x, head.y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.strokeStyle = '#0f5132';
      ctx.lineWidth = 3;
      ctx.lineCap = 'round';
      [[[-2, -5], [-10, -1]], [[2, -5], [10, -10]], [[-2, 7], [-9, 15]], [[2, 7], [9, 13]]].forEach(([start, end]) => {
        const a = markerPoint(x, y, start[0], start[1], angle);
        const b = markerPoint(x, y, end[0], end[1], angle);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      });
      ctx.restore();
    }
    function drawPolygon(points, fill, stroke, width = 2, dashed = false) {
      if (points.length < 2) return;
      ctx.save();
      ctx.beginPath();
      points.forEach((p, index) => {
        const pt = latlonToXY(p.lat, p.lon);
        if (index === 0) ctx.moveTo(pt.x, pt.y); else ctx.lineTo(pt.x, pt.y);
      });
      ctx.closePath();
      if (fill) {
        ctx.fillStyle = fill;
        ctx.fill();
      }
      if (stroke) {
        if (dashed) ctx.setLineDash([8, 5]);
        ctx.strokeStyle = stroke;
        ctx.lineWidth = width;
        ctx.stroke();
      }
      ctx.restore();
    }
    function drawPresetAreas() {
      PRESET_TRACKS.forEach(track => {
        const isSelected = track.id === state.selectedTrackId;
        drawPolygon(
          markingAreaPolygon(track),
          isSelected ? 'rgba(37, 99, 235, .14)' : 'rgba(15, 23, 42, .05)',
          isSelected ? '#2563eb' : 'rgba(71, 85, 105, .55)',
          isSelected ? 3 : 1.5,
          !isSelected
        );
        (track.restrictedAreas || []).forEach(area => {
          if (area.type !== 'polygon') return;
          drawPolygon(area.points.map(normalizePoint), 'rgba(220, 38, 38, .18)', '#dc2626', 2, true);
        });
      });
    }
    function toast(text) {
      const el = document.getElementById('toast');
      el.textContent = text;
      el.style.display = 'block';
      clearTimeout(toast.timer);
      toast.timer = setTimeout(() => el.style.display = 'none', 5200);
    }
    async function post(path, body = {}) {
      const res = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || '操作失败');
      return data;
    }
    async function action(button, fn) {
      button.disabled = true;
      try { await fn(); } catch (err) { toast(err.message); }
      finally { button.disabled = false; }
    }
    const drag = {active: false, moved: false, x: 0, y: 0, center: null};
    canvas.addEventListener('mousedown', event => {
      drag.active = true;
      drag.moved = false;
      drag.x = event.clientX;
      drag.y = event.clientY;
      const displayCenter = mapPointForLayer(state.centerLat, state.centerLon);
      drag.center = latlonToWorld(displayCenter.lat, displayCenter.lon);
      canvas.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', event => {
      if (!drag.active) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (Math.hypot(dx, dy) > 3) drag.moved = true;
      const next = worldToLatlon(drag.center.x - dx, drag.center.y - dy);
      const center = state.layer === 'satellite' ? wgs84ToGcj02(next.lat, next.lon) : next;
      state.centerLat = center.lat;
      state.centerLon = center.lon;
      draw();
    });
    window.addEventListener('mouseup', () => {
      drag.active = false;
      canvas.style.cursor = 'crosshair';
    });
    let wheelZoomDelta = 0;
    let wheelZoomTimer = null;
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      wheelZoomDelta += event.deltaY;
      clearTimeout(wheelZoomTimer);
      wheelZoomTimer = setTimeout(() => wheelZoomDelta = 0, 180);
      if (Math.abs(wheelZoomDelta) < 120) return;

      const before = xyToLatlon(
        (event.clientX - canvas.getBoundingClientRect().left) * canvas.width / canvas.getBoundingClientRect().width,
        (event.clientY - canvas.getBoundingClientRect().top) * canvas.height / canvas.getBoundingClientRect().height
      );
      state.zoom = Math.max(state.minZoom, Math.min(maxZoomForLayer(), state.zoom + (wheelZoomDelta < 0 ? 1 : -1)));
      wheelZoomDelta = 0;
      state.centerLat = before.lat;
      state.centerLon = before.lon;
      draw();
    }, {passive: false});
    canvas.style.cursor = 'crosshair';
    canvas.addEventListener('click', event => {
      if (drag.moved) return;
      const rect = canvas.getBoundingClientRect();
      const x = (event.clientX - rect.left) * canvas.width / rect.width;
      const y = (event.clientY - rect.top) * canvas.height / rect.height;
      const hitIndex = hitRoutePoint(x, y);
      if (hitIndex !== null) {
        const p = state.route[hitIndex];
        state.selectedRoutePointIndex = hitIndex;
        fields.lat.value = p.lat.toFixed(6);
        fields.lon.value = p.lon.toFixed(6);
        draw();
        return;
      }

      const p = xyToLatlon(x, y);
      state.route.push(p);
      if (state.selectedRoutePointIndex === null) state.selectedRoutePointIndex = 0;
      state.current = p;
      fields.lat.value = p.lat.toFixed(6);
      fields.lon.value = p.lon.toFixed(6);
      draw();
    });
    document.getElementById('center').onclick = () => {
      state.centerLat = Number(fields.lat.value);
      state.centerLon = Number(fields.lon.value);
      state.current = {lat: state.centerLat, lon: state.centerLon};
      draw();
    };
    function setLayer(layer) {
      state.layer = layer;
      state.zoom = Math.min(state.zoom, maxZoomForLayer());
      document.getElementById('satelliteLayer').classList.toggle('active', layer === 'satellite');
      document.getElementById('standardLayer').classList.toggle('active', layer === 'standard');
      draw();
    }
    document.getElementById('satelliteLayer').onclick = () => setLayer('satellite');
    document.getElementById('standardLayer').onclick = () => setLayer('standard');
    document.getElementById('undo').onclick = () => {
      state.route.pop();
      if (!state.route.length) {
        state.selectedRoutePointIndex = null;
      } else if (state.selectedRoutePointIndex !== null) {
        state.selectedRoutePointIndex = Math.min(state.selectedRoutePointIndex, state.route.length - 1);
      }
      state.current = state.route.length ? state.route[state.route.length - 1] : null;
      draw();
    };
    document.getElementById('clearRoute').onclick = () => {
      state.route = [];
      state.selectedRoutePointIndex = null;
      state.current = null;
      draw();
    };
    document.getElementById('connect').onclick = event => action(event.currentTarget, async () => {
      deviceStatus.textContent = '正在连接...';
      const data = await post('/api/connect');
      deviceStatus.textContent = data.status;
    });
    document.getElementById('inject').onclick = event => action(event.currentTarget, async () => {
      await post('/api/inject', {lat: Number(fields.lat.value), lon: Number(fields.lon.value)});
      state.current = {lat: Number(fields.lat.value), lon: Number(fields.lon.value)};
      draw();
      toast('坐标已注入');
    });
    document.getElementById('clear').onclick = event => action(event.currentTarget, async () => {
      await post('/api/clear');
      state.current = null;
      draw();
      toast('虚拟定位已清除');
    });
    document.getElementById('startRun').onclick = event => action(event.currentTarget, async () => {
      if (state.route.length < 2) throw new Error('请先在地图上至少标两个路线点');
      await post('/api/route/start', {
        route: state.route,
        restricted_areas: (selectedTrack().restrictedAreas || [])
          .filter(area => area.type === 'polygon')
          .map(area => area.points.map(normalizePoint)),
        speed: Number(fields.speed.value),
        interval: Number(fields.interval.value),
        drift: Number(fields.drift.value),
        laps: Number(fields.laps.value),
        close_point_index: closePointIndex()
      });
      state.running = true;
      updateRouteStatus('跑步模拟已开始');
    });
    document.getElementById('stopRun').onclick = event => action(event.currentTarget, async () => {
      await post('/api/route/stop');
      state.running = false;
      updateRouteStatus('跑步模拟已停止');
    });
    async function pollState() {
      try {
        const res = await fetch('/api/state');
        const data = await res.json();
        if (data.current) {
          state.current = data.current;
          fields.lat.value = data.current.lat.toFixed(6);
          fields.lon.value = data.current.lon.toFixed(6);
          draw();
        }
        if (data.route_status) routeStatus.textContent = data.route_status;
      } catch {}
      setTimeout(pollState, 1200);
    }
    function locateRealPosition() {
      if (!navigator.geolocation) {
        toast('浏览器不支持获取真实位置');
        return;
      }

      navigator.geolocation.getCurrentPosition(position => {
        const point = wgs84ToGcj02(position.coords.latitude, position.coords.longitude);
        state.real = point;
        if (!state.selectedTrackId) {
          state.centerLat = point.lat;
          state.centerLon = point.lon;
        }
        draw();
      }, error => {
        toast(`无法获取真实位置：${error.message}`);
      }, {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 30000
      });

      navigator.geolocation.watchPosition(position => {
        state.real = wgs84ToGcj02(position.coords.latitude, position.coords.longitude);
        draw();
      }, () => {}, {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 30000
      });
    }
    renderTrackOptions();
    selectTrack(GENERATION_RULES.defaultTrackId);
    locateRealPosition();
    pollState();
  </script>
</body>
</html>
"""


class Point(BaseModel):
    lat: float
    lon: float


class InjectRequest(BaseModel):
    lat: float
    lon: float


class RouteStartRequest(BaseModel):
    route: list[Point]
    restricted_areas: list[list[Point]] = Field(default_factory=list)
    speed: float
    interval: float
    drift: float
    laps: int = 6
    close_point_index: int = 0


class WebState:
    def __init__(self) -> None:
        self.controller = IOSLocationController()
        self.lock = threading.Lock()
        self.current: dict[str, float] | None = None
        self.route_status = ""
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"current": self.current, "route_status": self.route_status}

    def set_current(self, lat: float, lon: float, heading: Optional[float] = None) -> None:
        with self.lock:
            payload = {"lat": lat, "lon": lon}
            if heading is not None:
                payload["heading"] = heading
            elif self.current and "heading" in self.current:
                payload["heading"] = self.current["heading"]
            self.current = payload

    def clear_current(self) -> None:
        with self.lock:
            self.current = None

    def set_route_status(self, text: str) -> None:
        with self.lock:
            self.route_status = text


state = WebState()
app = FastAPI()


def ok(**payload: Any) -> JSONResponse:
    return JSONResponse({"ok": True, **payload})


def fail(exc: Exception) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


@app.get("/api/state")
def api_state() -> dict[str, Any]:
    return state.snapshot()


@app.post("/api/connect")
def api_connect() -> JSONResponse:
    try:
        status = state.controller.connect()
        return ok(status=status)
    except Exception as exc:
        return fail(exc)


@app.post("/api/inject")
def api_inject(req: InjectRequest) -> JSONResponse:
    try:
        wgs_lat, wgs_lon = gcj02_to_wgs84(req.lat, req.lon)
        state.controller.set_location(wgs_lat, wgs_lon)
        state.set_current(req.lat, req.lon)
        return ok()
    except Exception as exc:
        return fail(exc)


@app.post("/api/clear")
def api_clear() -> JSONResponse:
    try:
        state.controller.clear_location()
        state.clear_current()
        return ok()
    except Exception as exc:
        return fail(exc)


@app.post("/api/route/start")
def api_route_start(req: RouteStartRequest) -> JSONResponse:
    if len(req.route) < 2:
        return JSONResponse({"ok": False, "error": "请先在地图上至少标两个路线点"}, status_code=400)
    if req.speed <= 0 or req.interval <= 0 or req.drift < 0 or req.laps < 1:
        return JSONResponse({"ok": False, "error": "速度和间隔必须大于 0，步态摆动不能小于 0，模拟圈数至少为 1"}, status_code=400)
    if state.thread and state.thread.is_alive():
        return JSONResponse({"ok": False, "error": "跑步模拟已经在进行中"}, status_code=400)

    points = [(point.lat, point.lon) for point in req.route]
    restricted_areas = [[(point.lat, point.lon) for point in area] for area in req.restricted_areas]
    close_point_index = max(0, min(req.close_point_index, len(points) - 1))
    route, lap_end_indices = build_loop_route(points, req.speed, req.interval, req.laps, close_point_index)
    route, lap_end_indices = limit_route_step_distance(route, lap_end_indices)
    swayed_route = build_running_sway_route(route, req.drift)
    if restricted_areas:
        swayed_route = [
            keep_point_outside_restricted_areas(point, restricted_areas)
            for point in swayed_route
        ]
    swayed_route, lap_end_indices = limit_route_step_distance(swayed_route, lap_end_indices)
    route = swayed_route
    plan_ok, plan_messages = validate_running_plan(swayed_route, req.speed, req.interval, req.drift)
    if not plan_ok:
        return JSONResponse({"ok": False, "error": "\n".join(plan_messages)}, status_code=400)

    lap_end_set = set(lap_end_indices)
    state.stop_event.clear()
    state.set_route_status(
        f"跑步模拟中：闭合到第 {close_point_index + 1} 个点，共 {req.laps} 圈 / {len(route)} 个注入点；"
        f"{plan_messages[-1]}；约 {AUTO_STOP_DISTANCE_METERS:.0f} 米自动停止"
    )

    def runner() -> None:
        try:
            first_lat, first_lon = swayed_route[0]
            first_wgs_lat, first_wgs_lon = gcj02_to_wgs84(first_lat, first_lon)
            state.controller.set_location(first_wgs_lat, first_wgs_lon)
            first_heading = bearing_degrees(swayed_route[0], swayed_route[1]) if len(swayed_route) > 1 else 0.0
            state.set_current(first_lat, first_lon, first_heading)
            state.set_route_status(f"首点稳定中：约 {LOCATION_SETTLE_SECONDS:.0f} 秒后开始移动")
            if state.stop_event.wait(LOCATION_SETTLE_SECONDS):
                return

            next_tick = time.monotonic()
            last_injected_point: Optional[tuple[float, float]] = None
            last_injected_at: Optional[float] = None
            injected_distance = 0.0
            auto_stopped = False
            for index, _point in enumerate(route, start=1):
                if state.stop_event.is_set():
                    break

                now = time.monotonic()
                if next_tick < now - req.interval:
                    next_tick = now

                wait_seconds = next_tick - time.monotonic()
                if wait_seconds > 0 and state.stop_event.wait(wait_seconds):
                    break

                drift_lat, drift_lon = swayed_route[index - 1]
                point_distance = 0.0
                if last_injected_point is not None and last_injected_at is not None:
                    point_distance = distance_m(last_injected_point, (drift_lat, drift_lon))
                    min_elapsed = point_distance / GPS_JUMP_GUARD_SPEED_MPS
                    guard_wait = min_elapsed - (time.monotonic() - last_injected_at)
                    if guard_wait > 0 and state.stop_event.wait(guard_wait):
                        break

                wgs_lat, wgs_lon = gcj02_to_wgs84(drift_lat, drift_lon)
                state.controller.set_location(wgs_lat, wgs_lon)
                injected_distance += point_distance
                heading = (
                    bearing_degrees(last_injected_point, (drift_lat, drift_lon))
                    if last_injected_point is not None and point_distance > 0.05
                    else None
                )
                last_injected_point = (drift_lat, drift_lon)
                last_injected_at = time.monotonic()
                state.set_current(drift_lat, drift_lon, heading)
                completed_laps = sum(1 for end_index in lap_end_indices if index >= end_index)
                state.set_route_status(
                    f"跑步模拟中：{index}/{len(route)}  已完成 {completed_laps}/{req.laps} 圈  {injected_distance:.0f} 米"
                )

                if index in lap_end_set:
                    state.set_route_status(f"已回到第 {close_point_index + 1} 个标定点：完成 {completed_laps}/{req.laps} 圈")

                next_tick += req.interval
                if injected_distance >= AUTO_STOP_DISTANCE_METERS:
                    auto_stopped = True
                    state.set_route_status(f"已达到 {injected_distance:.0f} 米，自动停止并保持最后位置")
                    break

            if state.stop_event.is_set():
                state.set_route_status("跑步模拟已停止")
            elif auto_stopped:
                pass
            else:
                state.set_route_status("跑步模拟完成")
        except Exception as exc:
            state.set_route_status(f"跑步模拟错误：{exc}")
        finally:
            state.stop_event.clear()

    state.thread = threading.Thread(target=runner, daemon=True)
    state.thread.start()
    return ok()


@app.post("/api/route/stop")
def api_route_stop() -> JSONResponse:
    state.stop_event.set()
    return ok()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(port: Optional[int] = None, open_browser: bool = True) -> None:
    port = port or free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"浏览器界面已启动：{url}", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    run()
