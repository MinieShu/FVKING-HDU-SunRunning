import asyncio
import math
import os
import platform
import random
import threading
import traceback
import base64
import tkinter as tk
import urllib.request
from tkinter import messagebox, ttk
from typing import Optional


PI = math.pi
A = 6378245.0
EE = 0.00669342162296594323


def _out_of_china(lat: float, lon: float) -> bool:
    return not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y
    ret += 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * PI) + 320.0 * math.sin(y * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x
    ret += 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    if _out_of_china(lat, lon):
        return lat, lon

    d_lat = _transform_lat(lon - 105.0, lat - 35.0)
    d_lon = _transform_lon(lon - 105.0, lat - 35.0)

    rad_lat = lat / 180.0 * PI
    magic = math.sin(rad_lat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)

    d_lat = (d_lat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * PI)
    d_lon = (d_lon * 180.0) / (A / sqrt_magic * math.cos(rad_lat) * PI)

    return lat + d_lat, lon + d_lon


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    if _out_of_china(lat, lon):
        return lat, lon

    gcj_lat, gcj_lon = wgs84_to_gcj02(lat, lon)
    return lat * 2 - gcj_lat, lon * 2 - gcj_lon


def meters_per_lon_degree(lat: float) -> float:
    return 111320.0 * math.cos(math.radians(lat))


def mercator_world_size(zoom: int, tile_size: int = 256) -> int:
    return tile_size * (2**zoom)


def latlon_to_world_pixel(lat: float, lon: float, zoom: int, tile_size: int = 256) -> tuple[float, float]:
    size = mercator_world_size(zoom, tile_size)
    clamped_lat = max(-85.05112878, min(85.05112878, lat))
    sin_lat = math.sin(math.radians(clamped_lat))
    x = (lon + 180.0) / 360.0 * size
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * size
    return x, y


def world_pixel_to_latlon(x: float, y: float, zoom: int, tile_size: int = 256) -> tuple[float, float]:
    size = mercator_world_size(zoom, tile_size)
    lon = x / size * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / size
    lat = math.degrees(math.atan(0.5 * (math.exp(n) - math.exp(-n))))
    return lat, lon


def amap_tile_url(x: int, y: int, zoom: int) -> str:
    subdomain = abs(x + y) % 4 + 1
    return (
        f"https://webrd0{subdomain}.is.autonavi.com/appmaptile"
        f"?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={zoom}"
    )


def distance_m(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    lat_a, lon_a = point_a
    lat_b, lon_b = point_b
    mean_lat = (lat_a + lat_b) / 2
    dx = (lon_b - lon_a) * meters_per_lon_degree(mean_lat)
    dy = (lat_b - lat_a) * 111320.0
    return math.hypot(dx, dy)


def interpolate_route(
    points: list[tuple[float, float]], speed_kmh: float, interval_seconds: float
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points[:]

    step_meters = max(0.5, speed_kmh * 1000 / 3600 * interval_seconds)
    route: list[tuple[float, float]] = [points[0]]

    for start, end in zip(points, points[1:]):
        segment_distance = distance_m(start, end)
        step_count = max(1, int(math.ceil(segment_distance / step_meters)))
        for index in range(1, step_count + 1):
            ratio = index / step_count
            route.append(
                (
                    start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio,
                )
            )

    return route


def add_location_drift(lat: float, lon: float, max_drift_meters: float) -> tuple[float, float]:
    if max_drift_meters <= 0:
        return lat, lon

    angle = random.uniform(0, math.tau)
    radius = random.uniform(0, max_drift_meters)
    drift_lat = math.sin(angle) * radius / 111320.0
    lon_scale = max(1.0, meters_per_lon_degree(lat))
    drift_lon = math.cos(angle) * radius / lon_scale
    return lat + drift_lat, lon + drift_lon


class IOSLocationController:
    def __init__(self) -> None:
        self.lockdown = None
        self.device_name = ""
        self.product_version = ""

    def connect(self) -> str:
        asyncio.run(self._connect_async())
        return self.status_text

    async def _connect_async(self) -> None:
        try:
            from pymobiledevice3.lockdown import LockdownPairingController

            controller = LockdownPairingController()
            self.lockdown = await controller.get_lockdown_client()
        except (ImportError, AttributeError):
            try:
                from pymobiledevice3.lockdown import create_using_usbmux
            except ImportError:
                from pymobiledevice3.lockdown import LockdownClient

                self.lockdown = LockdownClient()
            else:
                self.lockdown = await create_using_usbmux(autopair=True)

        values = getattr(self.lockdown, "all_values", {}) or {}
        self.device_name = values.get("DeviceName", "iOS Device")
        self.product_version = getattr(self.lockdown, "product_version", values.get("ProductVersion", ""))

        await self._check_developer_disk_image()

    @property
    def status_text(self) -> str:
        if not self.lockdown:
            return "未连接"
        version = f" iOS {self.product_version}" if self.product_version else ""
        return f"已连接：{self.device_name}{version}"

    async def _check_developer_disk_image(self) -> bool:
        if not self.lockdown:
            raise RuntimeError("设备未连接")

        try:
            from pymobiledevice3.exceptions import NotMountedError
            from pymobiledevice3.services.mobile_image_mounter import MobileImageMounterService

            mounter = MobileImageMounterService(lockdown=self.lockdown)
            await mounter.lookup_image("Developer")
            return True
        except NotMountedError:
            print("提示：DeveloperDiskImage 未挂载，请先执行：python3 -m pymobiledevice3 mounter auto-mount")
            return False
        except Exception as exc:
            print(f"提示：无法确认 DeveloperDiskImage 状态：{exc}")
            return False

    def _get_dvt_class(self):
        try:
            from pymobiledevice3.services.dvt.dvt_secure_channel_connection import DvtSecureChannelConnection

            return DvtSecureChannelConnection
        except ImportError:
            from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService

            return DvtSecureSocketProxyService

    def set_location(self, lat: float, lon: float) -> None:
        if not self.lockdown:
            raise RuntimeError("设备未连接")

        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        dvt_class = self._get_dvt_class()
        with dvt_class(lockdown=self.lockdown) as dvt:
            LocationSimulation(dvt).set(lat, lon)

    def clear_location(self) -> None:
        if not self.lockdown:
            raise RuntimeError("设备未连接")

        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        dvt_class = self._get_dvt_class()
        with dvt_class(lockdown=self.lockdown) as dvt:
            LocationSimulation(dvt).clear()


class IOSLocationApp:
    MAP_WIDTH = 620
    MAP_HEIGHT = 460
    TILE_SIZE = 256
    ZOOM = 17

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.controller = IOSLocationController()
        self.route_points: list[tuple[float, float]] = []
        self.current_position: Optional[tuple[float, float]] = None
        self.simulation_stop = threading.Event()
        self.simulation_thread: Optional[threading.Thread] = None

        self.root.title("iOS 虚拟定位注入工具")
        self.root.geometry("980x620")
        self.root.minsize(900, 560)

        self.status_var = tk.StringVar(value="未连接")
        self.lat_var = tk.StringVar(value="39.908823")
        self.lon_var = tk.StringVar(value="116.397470")
        self.speed_var = tk.StringVar(value="8.0")
        self.interval_var = tk.StringVar(value="2.0")
        self.drift_var = tk.StringVar(value="6.0")
        self.route_status_var = tk.StringVar(value="在地图上点击添加跑步路线点")
        self.center_lat = 39.908823
        self.center_lon = 116.397470
        self._tile_cache: dict[tuple[int, int, int], tk.PhotoImage] = {}
        self._map_images: list[tk.PhotoImage] = []

        self._build_ui()
        self._redraw_map()
        self.root.update_idletasks()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=0)
        self.root.rowconfigure(0, weight=1)

        map_frame = ttk.Frame(self.root, padding=12)
        map_frame.grid(row=0, column=0, sticky="nsew")
        map_frame.columnconfigure(0, weight=1)
        map_frame.rowconfigure(1, weight=1)

        ttk.Label(map_frame, textvariable=self.route_status_var).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.map_canvas = tk.Canvas(
            map_frame,
            width=self.MAP_WIDTH,
            height=self.MAP_HEIGHT,
            bg="#edf2f7",
            highlightthickness=1,
            highlightbackground="#b7c1cc",
        )
        self.map_canvas.grid(row=1, column=0, sticky="nsew")
        self.map_canvas.bind("<Button-1>", self.add_route_point)

        control_frame = ttk.Frame(self.root, padding=16)
        control_frame.grid(row=0, column=1, sticky="ns")

        ttk.Label(control_frame, text="设备状态").grid(row=0, column=0, sticky="w")
        ttk.Label(control_frame, textvariable=self.status_var, foreground="#1f6feb", wraplength=230).grid(
            row=1, column=0, sticky="w", pady=(2, 12)
        )

        self._add_labeled_entry(control_frame, "纬度", self.lat_var, 2)
        self._add_labeled_entry(control_frame, "经度", self.lon_var, 4)
        ttk.Button(control_frame, text="定位到输入坐标", command=self.center_on_input).grid(
            row=6, column=0, sticky="ew", pady=(2, 14)
        )

        ttk.Button(control_frame, text="连接/刷新设备", command=self.connect_device).grid(row=7, column=0, sticky="ew")
        ttk.Button(control_frame, text="注入当前坐标", command=self.inject_location).grid(
            row=8, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(control_frame, text="清除虚拟定位", command=self.clear_location).grid(
            row=9, column=0, sticky="ew", pady=(8, 18)
        )

        self._add_labeled_entry(control_frame, "跑步速度 km/h", self.speed_var, 10)
        self._add_labeled_entry(control_frame, "注入间隔 秒", self.interval_var, 12)
        self._add_labeled_entry(control_frame, "随机浮动 米", self.drift_var, 14)

        ttk.Button(control_frame, text="开始轨迹跑步", command=self.start_route_simulation).grid(
            row=16, column=0, sticky="ew", pady=(10, 0)
        )
        ttk.Button(control_frame, text="停止跑步", command=self.stop_route_simulation).grid(
            row=17, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(control_frame, text="撤销上一个点", command=self.undo_route_point).grid(
            row=18, column=0, sticky="ew", pady=(18, 0)
        )
        ttk.Button(control_frame, text="清空路线", command=self.clear_route_points).grid(
            row=19, column=0, sticky="ew", pady=(8, 0)
        )

    def _add_labeled_entry(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=26).grid(row=row + 1, column=0, sticky="ew", pady=(2, 10))

    def _map_origin(self) -> tuple[float, float]:
        return self.center_lat, self.center_lon

    def latlon_to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        center_lat, center_lon = self._map_origin()
        center_x, center_y = latlon_to_world_pixel(center_lat, center_lon, self.ZOOM, self.TILE_SIZE)
        point_x, point_y = latlon_to_world_pixel(lat, lon, self.ZOOM, self.TILE_SIZE)
        x = self.MAP_WIDTH / 2 + point_x - center_x
        y = self.MAP_HEIGHT / 2 + point_y - center_y
        return x, y

    def xy_to_latlon(self, x: float, y: float) -> tuple[float, float]:
        center_lat, center_lon = self._map_origin()
        center_x, center_y = latlon_to_world_pixel(center_lat, center_lon, self.ZOOM, self.TILE_SIZE)
        return world_pixel_to_latlon(
            center_x + x - self.MAP_WIDTH / 2,
            center_y + y - self.MAP_HEIGHT / 2,
            self.ZOOM,
            self.TILE_SIZE,
        )

    def _redraw_map(self) -> None:
        self.map_canvas.delete("all")
        self._draw_map_background()

        if len(self.route_points) >= 2:
            path_xy = [self.latlon_to_xy(lat, lon) for lat, lon in self.route_points]
            self.map_canvas.create_line(*path_xy, fill="#e05a47", width=3, smooth=True)

        for index, (lat, lon) in enumerate(self.route_points, start=1):
            x, y = self.latlon_to_xy(lat, lon)
            fill = "#2b6cb0" if index == 1 else "#dd6b20"
            self.map_canvas.create_oval(x - 7, y - 7, x + 7, y + 7, fill=fill, outline="white", width=2)
            self.map_canvas.create_text(x, y - 18, text=str(index), fill="#1a202c", font=("Arial", 11, "bold"))

        if self.current_position:
            x, y = self.latlon_to_xy(*self.current_position)
            self.map_canvas.create_oval(x - 6, y - 6, x + 6, y + 6, fill="#15a46b", outline="white", width=2)
            self.map_canvas.create_text(x + 12, y, text="当前位置", anchor="w", fill="#0f5132")

        self._update_route_status()

    def _draw_map_background(self) -> None:
        if self._draw_amap_tiles():
            self._draw_map_scale_and_hint()
            return

        grid = 50
        for x in range(0, self.MAP_WIDTH + 1, grid):
            self.map_canvas.create_line(x, 0, x, self.MAP_HEIGHT, fill="#d6dee8")
        for y in range(0, self.MAP_HEIGHT + 1, grid):
            self.map_canvas.create_line(0, y, self.MAP_WIDTH, y, fill="#d6dee8")

        self.map_canvas.create_line(
            self.MAP_WIDTH / 2 - 10,
            self.MAP_HEIGHT / 2,
            self.MAP_WIDTH / 2 + 10,
            self.MAP_HEIGHT / 2,
            fill="#718096",
        )
        self.map_canvas.create_line(
            self.MAP_WIDTH / 2,
            self.MAP_HEIGHT / 2 - 10,
            self.MAP_WIDTH / 2,
            self.MAP_HEIGHT / 2 + 10,
            fill="#718096",
        )
        self._draw_map_scale_and_hint()

    def _draw_amap_tiles(self) -> bool:
        center_x, center_y = latlon_to_world_pixel(self.center_lat, self.center_lon, self.ZOOM, self.TILE_SIZE)
        top_left_x = center_x - self.MAP_WIDTH / 2
        top_left_y = center_y - self.MAP_HEIGHT / 2
        start_x = math.floor(top_left_x / self.TILE_SIZE)
        start_y = math.floor(top_left_y / self.TILE_SIZE)
        end_x = math.floor((top_left_x + self.MAP_WIDTH) / self.TILE_SIZE)
        end_y = math.floor((top_left_y + self.MAP_HEIGHT) / self.TILE_SIZE)
        max_tile = 2**self.ZOOM
        drew_any = False
        self._map_images = []

        for tile_x in range(start_x, end_x + 1):
            for tile_y in range(start_y, end_y + 1):
                if tile_y < 0 or tile_y >= max_tile:
                    continue
                wrapped_x = tile_x % max_tile
                key = (self.ZOOM, wrapped_x, tile_y)
                image = self._tile_cache.get(key)
                if image is None:
                    image = self._load_amap_tile(wrapped_x, tile_y, self.ZOOM)
                    if image is None:
                        continue
                    self._tile_cache[key] = image

                draw_x = round(tile_x * self.TILE_SIZE - top_left_x)
                draw_y = round(tile_y * self.TILE_SIZE - top_left_y)
                self.map_canvas.create_image(draw_x, draw_y, image=image, anchor="nw")
                self._map_images.append(image)
                drew_any = True

        return drew_any

    def _load_amap_tile(self, x: int, y: int, zoom: int) -> Optional[tk.PhotoImage]:
        try:
            req = urllib.request.Request(amap_tile_url(x, y, zoom), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=1.5) as response:
                data = base64.b64encode(response.read()).decode("ascii")
            return tk.PhotoImage(data=data)
        except Exception:
            return None

    def _draw_map_scale_and_hint(self) -> None:
        self.map_canvas.create_text(12, 14, text="点击地图添加路线点", anchor="w", fill="#2d3748")
        scale_x = self.MAP_WIDTH - 130
        scale_y = self.MAP_HEIGHT - 24
        self.map_canvas.create_line(scale_x, scale_y, scale_x + 100, scale_y, fill="#2d3748", width=3)
        meters_per_pixel = math.cos(math.radians(self.center_lat)) * 156543.03392 / (2**self.ZOOM)
        self.map_canvas.create_text(scale_x + 50, scale_y - 12, text=f"{meters_per_pixel * 100:.0f} 米", fill="#2d3748")

    def _update_route_status(self) -> None:
        if not self.route_points:
            self.route_status_var.set("在地图上点击添加跑步路线点")
            return

        total_distance = sum(distance_m(a, b) for a, b in zip(self.route_points, self.route_points[1:]))
        self.route_status_var.set(
            f"路线点：{len(self.route_points)} 个  距离约：{total_distance:.0f} 米"
        )

    def center_on_input(self) -> None:
        try:
            self.center_lat = float(self.lat_var.get().strip())
            self.center_lon = float(self.lon_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "纬度和经度必须是数字")
            return
        self.current_position = (self.center_lat, self.center_lon)
        self._redraw_map()

    def add_route_point(self, event: tk.Event) -> None:
        lat, lon = self.xy_to_latlon(event.x, event.y)
        self.route_points.append((lat, lon))
        self.lat_var.set(f"{lat:.6f}")
        self.lon_var.set(f"{lon:.6f}")
        self.current_position = (lat, lon)
        self._redraw_map()

    def undo_route_point(self) -> None:
        if self.route_points:
            self.route_points.pop()
            self.current_position = self.route_points[-1] if self.route_points else None
            self._redraw_map()

    def clear_route_points(self) -> None:
        self.route_points.clear()
        self.current_position = None
        self._redraw_map()

    def _run_background(self, task, success_message: Optional[str] = None) -> None:
        def runner() -> None:
            try:
                result = task()
                if result:
                    self.root.after(0, lambda: self.status_var.set(result))
                if success_message:
                    self.root.after(0, lambda: messagebox.showinfo("完成", success_message))
            except Exception as exc:
                self.root.after(0, lambda exc=exc: messagebox.showerror("错误", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def connect_device(self) -> None:
        self.status_var.set("正在连接...")
        self._run_background(lambda: self.controller.connect())

    def inject_location(self) -> None:
        def task() -> None:
            gcj_lat = float(self.lat_var.get().strip())
            gcj_lon = float(self.lon_var.get().strip())
            wgs_lat, wgs_lon = gcj02_to_wgs84(gcj_lat, gcj_lon)
            print(f"GCJ-02: {gcj_lat}, {gcj_lon}")
            print(f"WGS-84: {wgs_lat}, {wgs_lon}")
            self.controller.set_location(wgs_lat, wgs_lon)
            self.root.after(0, lambda: self._set_current_position(gcj_lat, gcj_lon))

        self._run_background(task, "坐标已注入")

    def clear_location(self) -> None:
        self._run_background(lambda: self.controller.clear_location(), "虚拟定位已清除")

    def _set_current_position(self, lat: float, lon: float) -> None:
        self.current_position = (lat, lon)
        self.lat_var.set(f"{lat:.6f}")
        self.lon_var.set(f"{lon:.6f}")
        self._redraw_map()

    def start_route_simulation(self) -> None:
        if len(self.route_points) < 2:
            messagebox.showerror("错误", "请先在地图上至少标两个路线点")
            return

        if self.simulation_thread and self.simulation_thread.is_alive():
            messagebox.showinfo("提示", "跑步模拟已经在进行中")
            return

        try:
            speed_kmh = float(self.speed_var.get().strip())
            interval_seconds = float(self.interval_var.get().strip())
            drift_meters = float(self.drift_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "速度、间隔和随机浮动必须是数字")
            return

        if speed_kmh <= 0 or interval_seconds <= 0 or drift_meters < 0:
            messagebox.showerror("错误", "速度和间隔必须大于 0，随机浮动不能小于 0")
            return

        route = interpolate_route(self.route_points, speed_kmh, interval_seconds)
        self.simulation_stop.clear()
        self.route_status_var.set(f"跑步模拟中：共 {len(route)} 个注入点")

        def runner() -> None:
            try:
                for index, (lat, lon) in enumerate(route, start=1):
                    if self.simulation_stop.is_set():
                        break

                    drift_lat, drift_lon = add_location_drift(lat, lon, drift_meters)
                    wgs_lat, wgs_lon = gcj02_to_wgs84(drift_lat, drift_lon)
                    self.controller.set_location(wgs_lat, wgs_lon)
                    self.root.after(0, lambda lat=drift_lat, lon=drift_lon: self._set_current_position(lat, lon))
                    self.root.after(
                        0,
                        lambda index=index, count=len(route): self.route_status_var.set(
                            f"跑步模拟中：{index}/{count}"
                        ),
                    )

                    if self.simulation_stop.wait(interval_seconds):
                        break

                if self.simulation_stop.is_set():
                    self.root.after(0, lambda: self.route_status_var.set("跑步模拟已停止"))
                else:
                    self.root.after(0, lambda: self.route_status_var.set("跑步模拟完成"))
            except Exception as exc:
                self.root.after(0, lambda exc=exc: messagebox.showerror("错误", str(exc)))
            finally:
                self.simulation_stop.clear()

        self.simulation_thread = threading.Thread(target=runner, daemon=True)
        self.simulation_thread.start()

    def stop_route_simulation(self) -> None:
        self.simulation_stop.set()


def show_startup_error(root: tk.Tk, exc: Exception) -> None:
    for child in root.winfo_children():
        child.destroy()

    root.title("iOS 虚拟定位注入工具 - 启动失败")
    root.geometry("860x520")

    frame = tk.Frame(root, padx=24, pady=24, bg="white")
    frame.pack(fill="both", expand=True)

    tk.Label(
        frame,
        text="界面启动失败",
        bg="white",
        fg="#b42318",
        font=("Arial", 18, "bold"),
        anchor="w",
    ).pack(fill="x")
    tk.Label(
        frame,
        text="下面是具体错误。把这段发给我，我就能继续定位。",
        bg="white",
        fg="#344054",
        anchor="w",
        pady=8,
    ).pack(fill="x")

    error_text = tk.Text(frame, height=18, wrap="word", bg="#f8fafc", fg="#101828")
    error_text.pack(fill="both", expand=True)
    error_text.insert("1.0", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    error_text.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    root.title("iOS 虚拟定位注入工具")

    tk_patchlevel = root.tk.call("info", "patchlevel")
    if (
        platform.system() == "Darwin"
        and str(tk_patchlevel).startswith("8.5")
        and os.environ.get("IOS_LOCATION_FORCE_TK") != "1"
    ):
        root.destroy()
        from ios_location_injector_web import run

        run()
        return

    loading = tk.Label(root, text="正在加载界面...", padx=24, pady=24, anchor="w")
    loading.pack(fill="both", expand=True)
    root.update_idletasks()

    try:
        loading.destroy()
        IOSLocationApp(root)
    except Exception as exc:
        show_startup_error(root, exc)

    root.mainloop()


if __name__ == "__main__":
    main()
