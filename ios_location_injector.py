import asyncio
import math
import os
import platform
import random
import threading
import traceback
import base64
import json
import subprocess
import sys
import tkinter as tk
import time
import urllib.request
from tkinter import messagebox, ttk
from typing import Optional

from packaging.version import Version


PI = math.pi
A = 6378245.0
EE = 0.00669342162296594323
MIN_ROUTE_POINT_DISTANCE_METERS = 1.25
MIN_VALID_RUNNING_SPEED_KMH = 2.2
MAX_VALID_RUNNING_SPEED_KMH = 22.5
MAX_GUIDE_SWAY_METERS = 3.0
ROUTE_STEP_JITTER_RATIO = 0.08
MIN_MICRO_SWAY_METERS = 0.25
CLI_LOCATION_STARTUP_GRACE_SECONDS = 0.2
LOCATION_SETTLE_SECONDS = 6.0
GPS_JUMP_GUARD_SPEED_MPS = 6.0
MAX_SAFE_INJECT_STEP_METERS = 6.2
RESTRICTED_AREA_BUFFER_METERS = 0.5
AUTO_STOP_DISTANCE_METERS = 2050.0


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


def amap_tile_url(x: int, y: int, zoom: int, style: int = 6) -> str:
    if style == 6:
        return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}"

    subdomain = abs(x + y) % 4 + 1
    return (
        f"https://webrd0{subdomain}.is.autonavi.com/appmaptile"
        f"?lang=zh_cn&size=1&scale=1&style={style}&x={x}&y={y}&z={zoom}"
    )


def locate_real_position_by_ip() -> Optional[tuple[float, float]]:
    try:
        req = urllib.request.Request("https://ipapi.co/json/", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        lat = payload.get("latitude")
        lon = payload.get("longitude")
        if lat is None or lon is None:
            return None
        return wgs84_to_gcj02(float(lat), float(lon))
    except Exception:
        return None


def distance_m(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    lat_a, lon_a = point_a
    lat_b, lon_b = point_b
    mean_lat = (lat_a + lat_b) / 2
    dx = (lon_b - lon_a) * meters_per_lon_degree(mean_lat)
    dy = (lat_b - lat_a) * 111320.0
    return math.hypot(dx, dy)


def bearing_degrees(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    lat_a, lon_a = point_a
    lat_b, lon_b = point_b
    mean_lat = (lat_a + lat_b) / 2
    east_meters = (lon_b - lon_a) * meters_per_lon_degree(mean_lat)
    north_meters = (lat_b - lat_a) * 111320.0
    if abs(east_meters) < 1e-9 and abs(north_meters) < 1e-9:
        return 0.0
    return (math.degrees(math.atan2(east_meters, north_meters)) + 360.0) % 360.0


def interpolate_route(
    points: list[tuple[float, float]], speed_kmh: float, interval_seconds: float
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points[:]

    base_step_meters = max(MIN_ROUTE_POINT_DISTANCE_METERS * 1.12, speed_kmh * 1000 / 3600 * interval_seconds)
    rng = random.Random()
    route: list[tuple[float, float]] = [points[0]]

    for end in points[1:]:
        while True:
            start = route[-1]
            segment_distance = distance_m(start, end)
            step_meters = base_step_meters * rng.uniform(1 - ROUTE_STEP_JITTER_RATIO, 1 + ROUTE_STEP_JITTER_RATIO)
            step_meters = max(MIN_ROUTE_POINT_DISTANCE_METERS * 1.08, step_meters)
            if segment_distance < step_meters:
                break

            ratio = step_meters / segment_distance
            route.append(
                (
                    start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio,
                )
            )

        if distance_m(route[-1], end) >= MIN_ROUTE_POINT_DISTANCE_METERS:
            route.append(end)

    if route[-1] != points[-1] and len(route) > 1:
        route[-1] = points[-1]

    return route


def route_distance_stats(route: list[tuple[float, float]]) -> tuple[float, float, float]:
    if len(route) < 2:
        return 0.0, 0.0, 0.0

    distances = [distance_m(a, b) for a, b in zip(route, route[1:])]
    return sum(distances), min(distances), max(distances)


def limit_route_step_distance(
    route: list[tuple[float, float]],
    lap_end_indices: list[int],
    max_step_meters: float = MAX_SAFE_INJECT_STEP_METERS,
) -> tuple[list[tuple[float, float]], list[int]]:
    if len(route) < 2 or max_step_meters <= 0:
        return route[:], lap_end_indices[:]

    lap_end_set = set(lap_end_indices)
    limited: list[tuple[float, float]] = [route[0]]
    limited_lap_end_indices: list[int] = []

    for original_index, end in enumerate(route[1:], start=2):
        start = limited[-1]
        segment_distance = distance_m(start, end)
        insert_count = max(0, math.ceil(segment_distance / max_step_meters) - 1)

        for step_index in range(1, insert_count + 1):
            ratio = step_index / (insert_count + 1)
            limited.append(
                (
                    start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio,
                )
            )

        limited.append(end)
        if original_index in lap_end_set:
            limited_lap_end_indices.append(len(limited))

    return limited, limited_lap_end_indices


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False

    y, x = point
    inside = False
    for index, (lat_i, lon_i) in enumerate(polygon):
        lat_j, lon_j = polygon[index - 1]
        intersects = (lat_i > y) != (lat_j > y) and x < (
            (lon_j - lon_i) * (y - lat_i) / ((lat_j - lat_i) or 1e-12) + lon_i
        )
        if intersects:
            inside = not inside
    return inside


def _point_to_segment_vector_meters(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, float, float]:
    lat, lon = point
    start_lat, start_lon = start
    end_lat, end_lon = end
    mean_lat = (lat + start_lat + end_lat) / 3
    lon_scale = max(1.0, meters_per_lon_degree(mean_lat))
    px, py = lon * lon_scale, lat * 111320.0
    ax, ay = start_lon * lon_scale, start_lat * 111320.0
    bx, by = end_lon * lon_scale, end_lat * 111320.0
    dx, dy = bx - ax, by - ay
    ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1.0)))
    closest_x = ax + dx * ratio
    closest_y = ay + dy * ratio
    return px - closest_x, py - closest_y, lon_scale


def keep_point_outside_restricted_areas(
    point: tuple[float, float],
    restricted_areas: list[list[tuple[float, float]]],
    buffer_meters: float = RESTRICTED_AREA_BUFFER_METERS,
) -> tuple[float, float]:
    corrected = point
    for polygon in restricted_areas:
        if len(polygon) < 3:
            continue

        nearest: tuple[float, float, float] | None = None
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            vector_x, vector_y, lon_scale = _point_to_segment_vector_meters(corrected, start, end)
            distance = math.hypot(vector_x, vector_y)
            if nearest is None or distance < nearest[0]:
                nearest = (distance, vector_x, vector_y, lon_scale)

        if nearest is None:
            continue

        distance, vector_x, vector_y, lon_scale = nearest
        inside = _point_in_polygon(corrected, polygon)
        if not inside and distance >= buffer_meters:
            continue

        if distance < 1e-6:
            center_lat = sum(lat for lat, _lon in polygon) / len(polygon)
            center_lon = sum(lon for _lat, lon in polygon) / len(polygon)
            vector_x = (corrected[1] - center_lon) * lon_scale
            vector_y = (corrected[0] - center_lat) * 111320.0
            distance = math.hypot(vector_x, vector_y)
            if distance < 1e-6:
                vector_x, vector_y, distance = 1.0, 0.0, 1.0

        direction_x = vector_x / distance
        direction_y = vector_y / distance
        push_meters = -(distance + buffer_meters) if inside else (buffer_meters - distance)
        corrected = (
            corrected[0] + direction_y * push_meters / 111320.0,
            corrected[1] + direction_x * push_meters / lon_scale,
        )

    return corrected


def validate_running_plan(
    route: list[tuple[float, float]],
    speed_kmh: float,
    interval_seconds: float,
    sway_meters: float,
) -> tuple[bool, list[str]]:
    messages: list[str] = []
    total_distance, min_step, max_step = route_distance_stats(route)

    if speed_kmh < MIN_VALID_RUNNING_SPEED_KMH:
        messages.append(f"速度过慢，建议不低于 {MIN_VALID_RUNNING_SPEED_KMH:.1f} km/h")
    if speed_kmh > MAX_VALID_RUNNING_SPEED_KMH:
        messages.append(f"速度过快，建议不高于 {MAX_VALID_RUNNING_SPEED_KMH:.1f} km/h")
    if min_step and min_step < MIN_ROUTE_POINT_DISTANCE_METERS:
        messages.append(f"轨迹点间距过近，最小间距 {min_step:.1f} 米")
    if sway_meters > MAX_GUIDE_SWAY_METERS:
        messages.append(f"步态摆动过大，建议不超过 {MAX_GUIDE_SWAY_METERS:.1f} 米")

    if total_distance:
        duration = total_distance / max(speed_kmh * 1000 / 3600, 0.1)
        messages.append(
            f"路线预检：约 {total_distance:.0f} 米，预计 {duration / 60:.1f} 分钟，"
            f"点间距 {min_step:.1f}-{max_step:.1f} 米，注入间隔 {interval_seconds:.1f} 秒"
        )

    blocking = [item for item in messages if not item.startswith("路线预检")]
    return not blocking, messages


def build_loop_route(
    points: list[tuple[float, float]],
    speed_kmh: float,
    interval_seconds: float,
    laps: int,
    close_point_index: int = 0,
) -> tuple[list[tuple[float, float]], list[int]]:
    if len(points) < 2 or laps < 1:
        return points[:], []

    close_point_index = max(0, min(close_point_index, len(points) - 1))
    lap_points = points[close_point_index:] + points[: close_point_index + 1]

    lap_route = interpolate_route(lap_points, speed_kmh, interval_seconds)
    route: list[tuple[float, float]] = []
    lap_end_indices: list[int] = []

    for lap_index in range(laps):
        segment = lap_route if lap_index == 0 else lap_route[1:]
        route.extend(segment)
        lap_end_indices.append(len(route))

    return route, lap_end_indices


def build_running_sway_route(
    route: list[tuple[float, float]], max_sway_meters: float
) -> list[tuple[float, float]]:
    if not route:
        return []
    if max_sway_meters <= 0:
        return route[:]

    rng = random.Random()
    effective_sway = max(MIN_MICRO_SWAY_METERS, max_sway_meters)
    lateral_limit = min(effective_sway * 0.35, 1.6)
    forward_limit = min(effective_sway * 0.12, 0.45)
    lateral = rng.uniform(-lateral_limit, lateral_limit) * 0.25
    forward = rng.uniform(-forward_limit, forward_limit) * 0.25
    target_lateral = lateral
    target_forward = forward
    next_target_index = 0
    swayed_route: list[tuple[float, float]] = []

    for index, (lat, lon) in enumerate(route):
        if index >= next_target_index:
            target_lateral = rng.uniform(-lateral_limit, lateral_limit)
            target_forward = rng.uniform(-forward_limit, forward_limit)
            next_target_index = index + rng.randint(3, 7)

        lateral += (target_lateral - lateral) * 0.22 + rng.uniform(-0.05, 0.05) * lateral_limit
        forward += (target_forward - forward) * 0.18 + rng.uniform(-0.04, 0.04) * forward_limit
        lateral = max(-lateral_limit, min(lateral_limit, lateral))
        forward = max(-forward_limit, min(forward_limit, forward))

        prev_lat, prev_lon = route[max(0, index - 1)]
        next_lat, next_lon = route[min(len(route) - 1, index + 1)]
        mean_lat = (prev_lat + lat + next_lat) / 3
        lon_scale = max(1.0, meters_per_lon_degree(mean_lat))
        dx = (next_lon - prev_lon) * lon_scale
        dy = (next_lat - prev_lat) * 111320.0
        length = math.hypot(dx, dy)

        if length <= 0:
            swayed_route.append((lat, lon))
            continue

        tangent_x = dx / length
        tangent_y = dy / length
        normal_x = -tangent_y
        normal_y = tangent_x
        offset_x = normal_x * lateral + tangent_x * forward
        offset_y = normal_y * lateral + tangent_y * forward
        swayed_route.append((lat + offset_y / 111320.0, lon + offset_x / lon_scale))

    return swayed_route


class IOSLocationController:
    def __init__(self) -> None:
        self.lockdown = None
        self.device_name = ""
        self.product_version = ""
        self.location_process: Optional[subprocess.Popen] = None

    def connect(self) -> str:
        asyncio.run(self._connect_async())
        return self.status_text

    async def _create_lockdown_client(self):
        try:
            from pymobiledevice3.lockdown import LockdownPairingController

            controller = LockdownPairingController()
            return await controller.get_lockdown_client()
        except (ImportError, AttributeError):
            try:
                from pymobiledevice3.lockdown import create_using_usbmux
            except ImportError:
                from pymobiledevice3.lockdown import LockdownClient

                return LockdownClient()
            return await create_using_usbmux(autopair=True)

    async def _connect_async(self) -> None:
        self.lockdown = await self._create_lockdown_client()
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
            try:
                try:
                    await mounter.lookup_image("Developer")
                    return True
                except NotMountedError:
                    mounted_images = await mounter.copy_devices()
                    for image in mounted_images:
                        if not image.get("IsMounted"):
                            continue
                        if image.get("DiskImageType") == "Developer":
                            return True
                        if (
                            image.get("DiskImageType") == "Personalized"
                            and image.get("PersonalizedImageType") == "DeveloperDiskImage"
                        ):
                            return True
                        if image.get("MountPath") == "/System/Developer":
                            return True
                    raise
            finally:
                await mounter.close()
        except NotMountedError:
            print("提示：DeveloperDiskImage 未挂载，请先执行：python3 -m pymobiledevice3 mounter auto-mount")
            return False
        except Exception as exc:
            print(f"提示：无法确认 DeveloperDiskImage 状态：{exc}")
            return False

    def set_location(self, lat: float, lon: float) -> None:
        if not self.lockdown:
            raise RuntimeError("设备未连接")

        if Version(self.product_version) >= Version("17.0"):
            self._set_location_with_cli(lat, lon)
            return

        asyncio.run(self._set_location_async(lat, lon))

    def _stop_location_process(self) -> None:
        if self.location_process is None:
            return
        if self.location_process.poll() is None:
            self.location_process.terminate()
            try:
                self.location_process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                self.location_process.kill()
                self.location_process.communicate()
        self.location_process = None

    def _set_location_with_cli(self, lat: float, lon: float) -> None:
        self._stop_location_process()
        command = [
            sys.executable,
            "-m",
            "pymobiledevice3",
            "developer",
            "dvt",
            "simulate-location",
            "set",
            str(lat),
            str(lon),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(CLI_LOCATION_STARTUP_GRACE_SECONDS)
        if process.poll() is None:
            self.location_process = process
            return

        output = process.communicate()[0].strip()
        if "Unable to connect to Tunneld" in output:
            raise RuntimeError(
                "iOS 17+ 需要先启动 tunneld。请新开一个终端执行："
                "sudo python3 -m pymobiledevice3 remote tunneld"
            )
        raise RuntimeError(output or "定位注入失败")

    async def _set_location_async(self, lat: float, lon: float) -> None:
        lockdown = await self._create_lockdown_client()
        product_version = getattr(lockdown, "product_version", self.product_version or "0")
        if Version(product_version) >= Version("17.0"):
            raise RuntimeError("iOS 17+ 请使用 CLI tunnel 注入路径")

        from pymobiledevice3.services.simulate_location import DtSimulateLocation

        await DtSimulateLocation(lockdown).set(lat, lon)

    def clear_location(self) -> None:
        if not self.lockdown:
            raise RuntimeError("设备未连接")

        self._stop_location_process()
        if Version(self.product_version) >= Version("17.0"):
            self._clear_location_with_cli()
            return

        asyncio.run(self._clear_location_async())

    def _clear_location_with_cli(self) -> None:
        command = [
            sys.executable,
            "-m",
            "pymobiledevice3",
            "developer",
            "dvt",
            "simulate-location",
            "clear",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return
        output = (result.stdout + result.stderr).strip()
        if "Unable to connect to Tunneld" in output:
            raise RuntimeError(
                "iOS 17+ 需要先启动 tunneld。请新开一个终端执行："
                "sudo python3 -m pymobiledevice3 remote tunneld"
            )
        raise RuntimeError(output or "清除虚拟定位失败")

    async def _clear_location_async(self) -> None:
        lockdown = await self._create_lockdown_client()
        product_version = getattr(lockdown, "product_version", self.product_version or "0")
        if Version(product_version) >= Version("17.0"):
            raise RuntimeError("iOS 17+ 请使用 CLI tunnel 注入路径")

        from pymobiledevice3.services.simulate_location import DtSimulateLocation

        await DtSimulateLocation(lockdown).clear()


class IOSLocationApp:
    MAP_WIDTH = 620
    MAP_HEIGHT = 460
    TILE_SIZE = 256
    ZOOM = 19

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.controller = IOSLocationController()
        self.route_points: list[tuple[float, float]] = []
        self.selected_route_point_index: Optional[int] = None
        self.current_position: Optional[tuple[float, float]] = None
        self.current_heading_degrees = 0.0
        self.real_position: Optional[tuple[float, float]] = None
        self.simulation_stop = threading.Event()
        self.simulation_thread: Optional[threading.Thread] = None

        self.root.title("iOS 虚拟定位注入工具")
        self.root.geometry("980x620")
        self.root.minsize(900, 560)

        self.status_var = tk.StringVar(value="未连接")
        self.lat_var = tk.StringVar(value="39.908823")
        self.lon_var = tk.StringVar(value="116.397470")
        self.speed_var = tk.StringVar(value="10.0")
        self.interval_var = tk.StringVar(value="2.0")
        self.drift_var = tk.StringVar(value="0.5")
        self.laps_var = tk.StringVar(value="6")
        self.route_status_var = tk.StringVar(value="在地图上点击添加跑步路线点")
        self.center_lat = 39.908823
        self.center_lon = 116.397470
        self._tile_cache: dict[tuple[int, int, int], tk.PhotoImage] = {}
        self._map_images: list[tk.PhotoImage] = []

        self._build_ui()
        self._redraw_map()
        self.root.update_idletasks()
        self._locate_real_position()

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
        self._add_labeled_entry(control_frame, "步态摆动 米", self.drift_var, 14)
        self._add_labeled_entry(control_frame, "模拟圈数", self.laps_var, 16)

        ttk.Button(control_frame, text="开始轨迹跑步", command=self.start_route_simulation).grid(
            row=18, column=0, sticky="ew", pady=(10, 0)
        )
        ttk.Button(control_frame, text="停止跑步", command=self.stop_route_simulation).grid(
            row=19, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(control_frame, text="撤销上一个点", command=self.undo_route_point).grid(
            row=20, column=0, sticky="ew", pady=(18, 0)
        )
        ttk.Button(control_frame, text="清空路线", command=self.clear_route_points).grid(
            row=21, column=0, sticky="ew", pady=(8, 0)
        )

    def _add_labeled_entry(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=26).grid(row=row + 1, column=0, sticky="ew", pady=(2, 10))

    def _map_origin(self) -> tuple[float, float]:
        return self.center_lat, self.center_lon

    def latlon_to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        center_lat, center_lon = self._map_origin()
        center_lat, center_lon = gcj02_to_wgs84(center_lat, center_lon)
        lat, lon = gcj02_to_wgs84(lat, lon)
        center_x, center_y = latlon_to_world_pixel(center_lat, center_lon, self.ZOOM, self.TILE_SIZE)
        point_x, point_y = latlon_to_world_pixel(lat, lon, self.ZOOM, self.TILE_SIZE)
        x = self.MAP_WIDTH / 2 + point_x - center_x
        y = self.MAP_HEIGHT / 2 + point_y - center_y
        return x, y

    def xy_to_latlon(self, x: float, y: float) -> tuple[float, float]:
        center_lat, center_lon = self._map_origin()
        center_lat, center_lon = gcj02_to_wgs84(center_lat, center_lon)
        center_x, center_y = latlon_to_world_pixel(center_lat, center_lon, self.ZOOM, self.TILE_SIZE)
        lat, lon = world_pixel_to_latlon(
            center_x + x - self.MAP_WIDTH / 2,
            center_y + y - self.MAP_HEIGHT / 2,
            self.ZOOM,
            self.TILE_SIZE,
        )
        return wgs84_to_gcj02(lat, lon)

    def _redraw_map(self) -> None:
        self.map_canvas.delete("all")
        self._draw_map_background()

        if len(self.route_points) >= 2:
            path_xy = [self.latlon_to_xy(lat, lon) for lat, lon in self.route_points]
            self.map_canvas.create_line(*path_xy, fill="#e05a47", width=3, smooth=True)
            first_x, first_y = self.latlon_to_xy(*self.route_points[0])
            last_x, last_y = self.latlon_to_xy(*self.route_points[-1])
            self.map_canvas.create_line(
                last_x,
                last_y,
                first_x,
                first_y,
                fill="#16a34a",
                width=3,
                dash=(7, 5),
            )

        for index, (lat, lon) in enumerate(self.route_points, start=1):
            x, y = self.latlon_to_xy(lat, lon)
            fill = "#2b6cb0" if index == 1 else "#dd6b20"
            selected = self.selected_route_point_index == index - 1
            radius = 12 if selected else 7
            outline = "#22c55e" if selected else "white"
            width = 3 if selected else 2
            self.map_canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill=fill,
                outline=outline,
                width=width,
            )
            self.map_canvas.create_text(x, y - radius - 11, text=str(index), fill="#1a202c", font=("Arial", 11, "bold"))

        if self.real_position:
            x, y = self.latlon_to_xy(*self.real_position)
            self.map_canvas.create_oval(
                x - 9,
                y - 9,
                x + 9,
                y + 9,
                outline="#2563eb",
                width=2,
                dash=(4, 3),
            )
            self.map_canvas.create_text(x + 13, y, text="真实位置", anchor="w", fill="#1d4ed8")

        if self.current_position:
            x, y = self.latlon_to_xy(*self.current_position)
            self._draw_runner_marker(x, y, self.current_heading_degrees)
            self.map_canvas.create_text(x + 13, y, text="虚拟定位", anchor="w", fill="#0f5132")

        self._update_route_status()

    def _rotate_marker_point(self, x: float, y: float, angle_radians: float) -> tuple[float, float]:
        cos_a = math.cos(angle_radians)
        sin_a = math.sin(angle_radians)
        return x * cos_a - y * sin_a, x * sin_a + y * cos_a

    def _marker_point(self, center_x: float, center_y: float, x: float, y: float, angle_radians: float) -> tuple[float, float]:
        rotated_x, rotated_y = self._rotate_marker_point(x, y, angle_radians)
        return center_x + rotated_x, center_y + rotated_y

    def _draw_runner_marker(self, x: float, y: float, heading_degrees: float) -> None:
        angle = math.radians(heading_degrees)
        body = [self._marker_point(x, y, px, py, angle) for px, py in [(-6, 5), (0, -12), (6, 5), (0, 10)]]
        self.map_canvas.create_polygon(*body, fill="#15a46b", outline="white", width=2)
        head_x, head_y = self._marker_point(x, y, 0, -18, angle)
        self.map_canvas.create_oval(head_x - 4, head_y - 4, head_x + 4, head_y + 4, fill="#0f5132", outline="white", width=1)
        for start, end in [((-2, -5), (-10, -1)), ((2, -5), (10, -10)), ((-2, 7), (-9, 15)), ((2, 7), (9, 13))]:
            x1, y1 = self._marker_point(x, y, *start, angle)
            x2, y2 = self._marker_point(x, y, *end, angle)
            self.map_canvas.create_line(x1, y1, x2, y2, fill="#0f5132", width=3, capstyle=tk.ROUND)

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
        self.map_canvas.create_text(12, 14, text="点击地图添加路线点，点击已有点设为闭合点", anchor="w", fill="#2d3748")
        scale_x = self.MAP_WIDTH - 130
        scale_y = self.MAP_HEIGHT - 24
        self.map_canvas.create_line(scale_x, scale_y, scale_x + 100, scale_y, fill="#2d3748", width=3)
        meters_per_pixel = math.cos(math.radians(self.center_lat)) * 156543.03392 / (2**self.ZOOM)
        self.map_canvas.create_text(scale_x + 50, scale_y - 12, text=f"{meters_per_pixel * 100:.0f} 米", fill="#2d3748")

    def _update_route_status(self) -> None:
        if not self.route_points:
            self.route_status_var.set("在地图上点击添加跑步路线点")
            return

        close_index = self._close_point_index()
        lap_points = self.route_points[close_index:] + self.route_points[: close_index + 1]
        total_distance = sum(distance_m(a, b) for a, b in zip(lap_points, lap_points[1:]))
        self.route_status_var.set(
            f"路线点：{len(self.route_points)} 个  闭合点：第 {close_index + 1} 个  单圈约：{total_distance:.0f} 米"
        )

    def _close_point_index(self) -> int:
        if len(self.route_points) < 2:
            return 0
        if self.selected_route_point_index is None:
            return 0
        return max(0, min(self.selected_route_point_index, len(self.route_points) - 1))

    def _hit_route_point(self, x: float, y: float) -> Optional[int]:
        nearest_index = None
        nearest_distance = 14.0
        for index, (lat, lon) in enumerate(self.route_points):
            point_x, point_y = self.latlon_to_xy(lat, lon)
            distance = math.hypot(point_x - x, point_y - y)
            if distance <= nearest_distance:
                nearest_index = index
                nearest_distance = distance
        return nearest_index

    def center_on_input(self) -> None:
        try:
            self.center_lat = float(self.lat_var.get().strip())
            self.center_lon = float(self.lon_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "纬度和经度必须是数字")
            return
        self.current_position = (self.center_lat, self.center_lon)
        self._redraw_map()

    def _locate_real_position(self) -> None:
        def task() -> Optional[tuple[float, float]]:
            return locate_real_position_by_ip()

        def runner() -> None:
            position = task()
            if position is None:
                return

            def apply_position() -> None:
                self.real_position = position
                self.center_lat, self.center_lon = position
                self._redraw_map()

            self.root.after(0, apply_position)

        threading.Thread(target=runner, daemon=True).start()

    def add_route_point(self, event: tk.Event) -> None:
        hit_index = self._hit_route_point(event.x, event.y)
        if hit_index is not None:
            self.selected_route_point_index = hit_index
            lat, lon = self.route_points[hit_index]
            self.lat_var.set(f"{lat:.6f}")
            self.lon_var.set(f"{lon:.6f}")
            self._redraw_map()
            return

        lat, lon = self.xy_to_latlon(event.x, event.y)
        self.route_points.append((lat, lon))
        if self.selected_route_point_index is None:
            self.selected_route_point_index = 0
        self.lat_var.set(f"{lat:.6f}")
        self.lon_var.set(f"{lon:.6f}")
        self.current_position = (lat, lon)
        self._redraw_map()

    def undo_route_point(self) -> None:
        if self.route_points:
            self.route_points.pop()
            if not self.route_points:
                self.selected_route_point_index = None
            elif self.selected_route_point_index is not None:
                self.selected_route_point_index = min(self.selected_route_point_index, len(self.route_points) - 1)
            self.current_position = self.route_points[-1] if self.route_points else None
            self._redraw_map()

    def clear_route_points(self) -> None:
        self.route_points.clear()
        self.selected_route_point_index = None
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
        def task() -> None:
            self.controller.clear_location()
            self.root.after(0, lambda: self._set_current_position(None))

        self._run_background(task, "虚拟定位已清除")

    def _set_current_position(
        self, lat: Optional[float], lon: Optional[float] = None, heading_degrees: Optional[float] = None
    ) -> None:
        if lat is None or lon is None:
            self.current_position = None
            self._redraw_map()
            return
        self.current_position = (lat, lon)
        if heading_degrees is not None:
            self.current_heading_degrees = heading_degrees
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
            laps = int(self.laps_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "速度、间隔、步态摆动和模拟圈数必须是数字")
            return

        if speed_kmh <= 0 or interval_seconds <= 0 or drift_meters < 0 or laps < 1:
            messagebox.showerror("错误", "速度和间隔必须大于 0，步态摆动不能小于 0，模拟圈数至少为 1")
            return

        close_point_index = self._close_point_index()
        route, lap_end_indices = build_loop_route(
            self.route_points, speed_kmh, interval_seconds, laps, close_point_index
        )
        route, lap_end_indices = limit_route_step_distance(route, lap_end_indices)
        swayed_route = build_running_sway_route(route, drift_meters)
        swayed_route, lap_end_indices = limit_route_step_distance(swayed_route, lap_end_indices)
        route = swayed_route
        plan_ok, plan_messages = validate_running_plan(swayed_route, speed_kmh, interval_seconds, drift_meters)
        if not plan_ok:
            messagebox.showerror("路线预检未通过", "\n".join(plan_messages))
            return

        lap_end_set = set(lap_end_indices)
        self.simulation_stop.clear()
        self.route_status_var.set(
            f"跑步模拟中：闭合到第 {close_point_index + 1} 个点，共 {laps} 圈 / {len(route)} 个注入点；"
            f"{plan_messages[-1]}；约 {AUTO_STOP_DISTANCE_METERS:.0f} 米自动停止"
        )

        def runner() -> None:
            try:
                first_lat, first_lon = swayed_route[0]
                first_wgs_lat, first_wgs_lon = gcj02_to_wgs84(first_lat, first_lon)
                self.controller.set_location(first_wgs_lat, first_wgs_lon)
                first_heading = bearing_degrees(swayed_route[0], swayed_route[1]) if len(swayed_route) > 1 else 0.0
                self.root.after(
                    0,
                    lambda lat=first_lat, lon=first_lon, heading=first_heading: self._set_current_position(
                        lat, lon, heading
                    ),
                )
                if self.simulation_stop.wait(LOCATION_SETTLE_SECONDS):
                    return

                next_tick = time.monotonic()
                last_injected_point: Optional[tuple[float, float]] = None
                last_injected_at: Optional[float] = None
                injected_distance = 0.0
                auto_stopped = False
                for index, _point in enumerate(route, start=1):
                    if self.simulation_stop.is_set():
                        break

                    now = time.monotonic()
                    if next_tick < now - interval_seconds:
                        next_tick = now

                    wait_seconds = next_tick - time.monotonic()
                    if wait_seconds > 0 and self.simulation_stop.wait(wait_seconds):
                        break

                    drift_lat, drift_lon = swayed_route[index - 1]
                    point_distance = 0.0
                    if last_injected_point is not None and last_injected_at is not None:
                        point_distance = distance_m(last_injected_point, (drift_lat, drift_lon))
                        min_elapsed = point_distance / GPS_JUMP_GUARD_SPEED_MPS
                        guard_wait = min_elapsed - (time.monotonic() - last_injected_at)
                        if guard_wait > 0 and self.simulation_stop.wait(guard_wait):
                            break

                    wgs_lat, wgs_lon = gcj02_to_wgs84(drift_lat, drift_lon)
                    self.controller.set_location(wgs_lat, wgs_lon)
                    injected_distance += point_distance
                    heading = (
                        bearing_degrees(last_injected_point, (drift_lat, drift_lon))
                        if last_injected_point is not None and point_distance > 0.05
                        else self.current_heading_degrees
                    )
                    last_injected_point = (drift_lat, drift_lon)
                    last_injected_at = time.monotonic()
                    self.root.after(
                        0,
                        lambda lat=drift_lat, lon=drift_lon, heading=heading: self._set_current_position(
                            lat, lon, heading
                        ),
                    )
                    completed_laps = sum(1 for end_index in lap_end_indices if index >= end_index)
                    self.root.after(
                        0,
                        lambda index=index, count=len(route), completed_laps=completed_laps, injected_distance=injected_distance: self.route_status_var.set(
                            f"跑步模拟中：{index}/{count}  已完成 {completed_laps}/{laps} 圈  {injected_distance:.0f} 米"
                        ),
                    )

                    if index in lap_end_set:
                        self.root.after(
                            0,
                            lambda completed_laps=completed_laps: self.route_status_var.set(
                                f"已回到第 {close_point_index + 1} 个标定点：完成 {completed_laps}/{laps} 圈"
                            ),
                        )

                    next_tick += interval_seconds
                    if injected_distance >= AUTO_STOP_DISTANCE_METERS:
                        auto_stopped = True
                        self.root.after(
                            0,
                            lambda injected_distance=injected_distance: self.route_status_var.set(
                                f"已达到 {injected_distance:.0f} 米，自动停止并保持最后位置"
                            ),
                        )
                        break

                if self.simulation_stop.is_set():
                    self.root.after(0, lambda: self.route_status_var.set("跑步模拟已停止"))
                elif auto_stopped:
                    pass
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
