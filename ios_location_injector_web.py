import socket
import threading
import webbrowser
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ios_location_injector import (
    IOSLocationController,
    add_location_drift,
    gcj02_to_wgs84,
    interpolate_route,
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
        <label>跑步速度 km/h</label>
        <input id="speed" value="8.0" inputmode="decimal" />
        <label>注入间隔 秒</label>
        <input id="interval" value="2.0" inputmode="decimal" />
        <label>随机浮动 米</label>
        <input id="drift" value="6.0" inputmode="decimal" />
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
      drift: document.getElementById('drift')
    };
    const state = {
      centerLat: 39.908823,
      centerLon: 116.397470,
      route: [],
      current: null,
      zoom: 17,
      minZoom: 3,
      maxZoom: 19,
      tileSize: 256,
      running: false
    };
    const tileCache = new Map();

    function metersPerLonDegree(lat) {
      return 111320.0 * Math.cos(lat * Math.PI / 180);
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
      const center = latlonToWorld(state.centerLat, state.centerLon);
      const point = latlonToWorld(lat, lon);
      return {
        x: canvas.width / 2 + point.x - center.x,
        y: canvas.height / 2 + point.y - center.y
      };
    }
    function xyToLatlon(x, y) {
      const center = latlonToWorld(state.centerLat, state.centerLon);
      return worldToLatlon(center.x + x - canvas.width / 2, center.y + y - canvas.height / 2);
    }
    function tileUrl(x, y, z) {
      const subdomain = Math.abs(x + y) % 4 + 1;
      return `https://webrd0${subdomain}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x=${x}&y=${y}&z=${z}`;
    }
    function drawMapTiles() {
      ctx.fillStyle = '#dce6ef';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      const center = latlonToWorld(state.centerLat, state.centerLon);
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
          const key = `${state.zoom}/${wrappedX}/${ty}`;
          const dx = Math.round(tx * state.tileSize - topLeft.x);
          const dy = Math.round(ty * state.tileSize - topLeft.y);
          let tile = tileCache.get(key);
          if (!tile) {
            tile = new Image();
            tile.onload = () => draw();
            tile.onerror = () => draw();
            tile.src = tileUrl(wrappedX, ty, state.zoom);
            tileCache.set(key, tile);
          }
          if (tile.complete && tile.naturalWidth > 0) {
            ctx.drawImage(tile, dx, dy, state.tileSize, state.tileSize);
          } else {
            loading = true;
            ctx.fillStyle = '#edf2f7';
            ctx.fillRect(dx, dy, state.tileSize, state.tileSize);
          }
        }
      }

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
      let total = 0;
      for (let i = 1; i < state.route.length; i++) total += distance(state.route[i - 1], state.route[i]);
      routeStatus.textContent = `路线点：${state.route.length} 个  距离约：${total.toFixed(0)} 米`;
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
      ctx.fillText('点击添加路线点，拖拽移动地图，滚轮缩放', 12, canvas.height - 48);
      const scaleX = canvas.width - 140;
      const scaleY = canvas.height - 28;
      ctx.strokeStyle = '#2d3748';
      ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(scaleX, scaleY); ctx.lineTo(scaleX + 100, scaleY); ctx.stroke();
      const metersPerPixel = Math.cos(state.centerLat * Math.PI / 180) * 156543.03392 / Math.pow(2, state.zoom);
      ctx.fillText(`${Math.round(metersPerPixel * 100)} 米`, scaleX + 28, scaleY - 10);

      if (state.route.length >= 2) {
        ctx.strokeStyle = '#e05a47';
        ctx.lineWidth = 3;
        ctx.beginPath();
        state.route.forEach((p, i) => {
          const pt = latlonToXY(p.lat, p.lon);
          if (i === 0) ctx.moveTo(pt.x, pt.y); else ctx.lineTo(pt.x, pt.y);
        });
        ctx.stroke();
      }
      state.route.forEach((p, index) => {
        const pt = latlonToXY(p.lat, p.lon);
        ctx.fillStyle = index === 0 ? '#2b6cb0' : '#dd6b20';
        ctx.strokeStyle = 'white';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 7, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#1a202c';
        ctx.font = 'bold 13px Arial';
        ctx.textAlign = 'center';
        ctx.fillText(String(index + 1), pt.x, pt.y - 15);
        ctx.textAlign = 'left';
      });
      if (state.current) {
        const pt = latlonToXY(state.current.lat, state.current.lon);
        ctx.fillStyle = '#15a46b';
        ctx.strokeStyle = 'white';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 6, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#0f5132';
        ctx.font = '14px Arial';
        ctx.fillText('当前位置', pt.x + 12, pt.y + 4);
      }
      updateRouteStatus();
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
      drag.center = latlonToWorld(state.centerLat, state.centerLon);
      canvas.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', event => {
      if (!drag.active) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (Math.hypot(dx, dy) > 3) drag.moved = true;
      const next = worldToLatlon(drag.center.x - dx, drag.center.y - dy);
      state.centerLat = next.lat;
      state.centerLon = next.lon;
      draw();
    });
    window.addEventListener('mouseup', () => {
      drag.active = false;
      canvas.style.cursor = 'crosshair';
    });
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      const before = xyToLatlon(
        (event.clientX - canvas.getBoundingClientRect().left) * canvas.width / canvas.getBoundingClientRect().width,
        (event.clientY - canvas.getBoundingClientRect().top) * canvas.height / canvas.getBoundingClientRect().height
      );
      state.zoom = Math.max(state.minZoom, Math.min(state.maxZoom, state.zoom + (event.deltaY < 0 ? 1 : -1)));
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
      const p = xyToLatlon(x, y);
      state.route.push(p);
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
    document.getElementById('undo').onclick = () => {
      state.route.pop();
      state.current = state.route.length ? state.route[state.route.length - 1] : null;
      draw();
    };
    document.getElementById('clearRoute').onclick = () => {
      state.route = [];
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
      toast('虚拟定位已清除');
    });
    document.getElementById('startRun').onclick = event => action(event.currentTarget, async () => {
      if (state.route.length < 2) throw new Error('请先在地图上至少标两个路线点');
      await post('/api/route/start', {
        route: state.route,
        speed: Number(fields.speed.value),
        interval: Number(fields.interval.value),
        drift: Number(fields.drift.value)
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
    draw();
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
    speed: float
    interval: float
    drift: float


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

    def set_current(self, lat: float, lon: float) -> None:
        with self.lock:
            self.current = {"lat": lat, "lon": lon}

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
        return ok()
    except Exception as exc:
        return fail(exc)


@app.post("/api/route/start")
def api_route_start(req: RouteStartRequest) -> JSONResponse:
    if len(req.route) < 2:
        return JSONResponse({"ok": False, "error": "请先在地图上至少标两个路线点"}, status_code=400)
    if req.speed <= 0 or req.interval <= 0 or req.drift < 0:
        return JSONResponse({"ok": False, "error": "速度和间隔必须大于 0，随机浮动不能小于 0"}, status_code=400)
    if state.thread and state.thread.is_alive():
        return JSONResponse({"ok": False, "error": "跑步模拟已经在进行中"}, status_code=400)

    points = [(point.lat, point.lon) for point in req.route]
    route = interpolate_route(points, req.speed, req.interval)
    state.stop_event.clear()
    state.set_route_status(f"跑步模拟中：共 {len(route)} 个注入点")

    def runner() -> None:
        try:
            for index, (lat, lon) in enumerate(route, start=1):
                if state.stop_event.is_set():
                    break

                drift_lat, drift_lon = add_location_drift(lat, lon, req.drift)
                wgs_lat, wgs_lon = gcj02_to_wgs84(drift_lat, drift_lon)
                state.controller.set_location(wgs_lat, wgs_lon)
                state.set_current(drift_lat, drift_lon)
                state.set_route_status(f"跑步模拟中：{index}/{len(route)}")

                if state.stop_event.wait(req.interval):
                    break

            if state.stop_event.is_set():
                state.set_route_status("跑步模拟已停止")
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
