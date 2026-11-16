from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import flet as ft

try:
    import psutil
except Exception:
    psutil = None


# ---------------------------------------------------------------------------
# 视觉常量
# ---------------------------------------------------------------------------
BG = "#0A0A0B"
SIDEBAR_BG = "#0C0D0F"
PANEL = "#101114"
ROW_HOVER = "#121318"
ACCENT = "#00E5FF"
TEXT = "#FFFFFF"
BODY = "#E2E8F0"
MUTED = "#64748B"
DIM = "#475569"
LINE = "#1A1B1E"
GREEN = "#22C55E"
AMBER = "#F59E0B"
RED = "#EF4444"

S1 = 8
S2 = 16
S3 = 24
S4 = 32
S5 = 40
S6 = 48
S8 = 64

WINDOW_W = 1280
WINDOW_H = 800
RADIUS = 12

def get_runtime_dir() -> Path:
    """源码运行时用脚本目录；PyInstaller 单文件运行时用 _MEIPASS。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parent


BASE_DIR = get_runtime_dir()
ASSET_DIR = BASE_DIR / "assets"
SOURCE_ASSET_ROOT = r"D:\douyin\推荐用的扩展\12"
AUTHOR_NAME = "伐浩"
AUTHOR_URL = (
    "https://www.douyin.com/user/"
    "MS4wLjABAAAApL6WdWT0Fi-l8bPuo5UpBDXhzYs0zC9i7V06NypCGmK4ZmO5GAKfSyHo_RYMsOm6"
)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


INSTANCE_PORT = 50617
INSTANCE_MAGIC = b"FLETMASTER_SINGLE_INSTANCE_V1"
_EXITING = threading.Event()


def _kill_flet_children() -> None:
    """先杀掉 Flet 客户端子进程，释放 _MEI 中的 DLL/文件句柄。"""
    if psutil is not None:
        try:
            current = psutil.Process(os.getpid())
            children = current.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except Exception:
                    pass
            psutil.wait_procs(children, timeout=1.0)
            return
        except Exception:
            pass

    # psutil 不可用时的兜底
    if os.name == "nt":
        try:
            taskkill = os.path.join(
                os.environ.get("SystemRoot", r"C:\Windows"),
                "System32",
                "taskkill.exe",
            )
            subprocess.Popen(
                [taskkill, "/PID", str(os.getpid()), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass


def hard_exit() -> None:
    """彻底退出：先杀 Flet 子进程，再强杀当前 Python 进程。"""
    if _EXITING.is_set():
        return
    _EXITING.set()
    _kill_flet_children()
    os._exit(0)


class SingleInstanceGuard:
    """
    单实例锁 + 普通/管理员权限交接。

    - 普通运行中，再开普通：新进程退出
    - 管理员运行中，再开普通：新进程退出
    - 普通运行中，开管理员：旧普通进程退出，管理员接管
    - 管理员运行中，再开管理员：新进程退出
    """

    def __init__(self, port: int = INSTANCE_PORT):
        self.port = port
        self.is_admin_mode = is_admin()
        self._server: socket.socket | None = None
        self._stop = threading.Event()

    def acquire(self) -> bool:
        for _ in range(20):
            server = self._try_bind()
            if server is not None:
                self._server = server
                threading.Thread(
                    target=self._serve,
                    name="FletSingleInstance",
                    daemon=True,
                ).start()
                return True

            reply = self._query_existing()
            if reply == b"OVERRIDE_OK":
                # 等待旧普通实例杀掉自己的 Flet 子进程并退出
                time.sleep(1.25)
                continue

            if reply in (b"NORMAL", b"ADMIN"):
                return False

            time.sleep(0.2)

        return False

    def _try_bind(self) -> socket.socket | None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                server.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            server.bind(("127.0.0.1", self.port))
            server.listen(8)
            server.settimeout(0.5)
            return server
        except OSError:
            server.close()
            return None

    def _query_existing(self) -> bytes | None:
        client = None
        try:
            client = socket.create_connection(
                ("127.0.0.1", self.port), timeout=0.5
            )
            client.settimeout(1.0)
            role = b"ADMIN" if self.is_admin_mode else b"USER"
            client.sendall(INSTANCE_MAGIC + b"|" + role + b"\n")
            response = client.recv(64).strip()
            if response in (b"NORMAL", b"ADMIN", b"OVERRIDE_OK"):
                return response
            return None
        except OSError:
            return None
        finally:
            if client is not None:
                client.close()

    def _serve(self) -> None:
        assert self._server is not None

        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                conn.settimeout(1.0)
                data = conn.recv(1024)

                if not data.startswith(INSTANCE_MAGIC):
                    conn.sendall(b"BUSY\n")
                    continue

                parts = data.split(b"|")
                client_is_admin = (
                    len(parts) >= 2 and parts[1].strip() == b"ADMIN"
                )

                if client_is_admin and not self.is_admin_mode:
                    conn.sendall(b"OVERRIDE_OK\n")
                    conn.close()

                    # 立刻释放端口，1 秒后杀旧进程
                    self._stop.set()
                    try:
                        self._server.close()
                    except Exception:
                        pass

                    threading.Timer(1.0, hard_exit).start()
                    return

                response = b"ADMIN\n" if self.is_admin_mode else b"NORMAL\n"
                conn.sendall(response)

            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self) -> None:
        self._stop.set()
        try:
            if self._server is not None:
                self._server.close()
        except Exception:
            pass


instance_guard: SingleInstanceGuard | None = None


def human_size(n: int) -> str:
    if n <= 0:
        return "0 MB"
    units = ("B", "KB", "MB", "GB", "TB")
    v = float(n)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:.2f} {units[i]}"


def human_gb(n: int) -> float:
    return round(n / (1024 ** 3), 2)


def fmt_time() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# 本地素材
# ---------------------------------------------------------------------------
def prepare_assets() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(SOURCE_ASSET_ROOT)
    wanted = {
        "logo.jpg": ("Logo", (".jpg", ".jpeg", ".png", ".webp")),
        "avatar.jpg": ("Developer", (".jpg", ".jpeg", ".png", ".webp")),
    }
    for name, (subdir, exts) in wanted.items():
        target = ASSET_DIR / name
        if target.exists():
            continue
        folder = source / subdir
        if not folder.exists():
            continue
        for candidate in sorted(folder.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in exts:
                try:
                    shutil.copy2(candidate, target)
                except Exception:
                    pass
                break


def has_asset(name: str) -> bool:
    p = ASSET_DIR / name
    return p.is_file() and p.stat().st_size > 0


def image_or_fallback(name: str, size: int, radius: int) -> ft.Control:
    if has_asset(name):
        return ft.Image(
            src=name,
            width=size,
            height=size,
            fit=ft.BoxFit.COVER,
            border_radius=radius,
        )
    return ft.Container(
        width=size,
        height=size,
        border_radius=radius,
        bgcolor=ACCENT,
        alignment=ft.Alignment.CENTER,
        content=ft.Text("W", color=BG, size=max(11, size // 2),
                        weight=ft.FontWeight.BOLD),
    )


# ---------------------------------------------------------------------------
# Windows DWM：Flet 窗口原生圆角 + 去描边
# ---------------------------------------------------------------------------
def _dwm_set(hwnd: int, attr: int, value: int) -> bool:
    try:
        v = ctypes.c_int(value)
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)
        ) == 0
    except Exception:
        return False


def find_flet_window() -> int:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def enum_proc(hwnd, lparam):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        pid = wt.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(
            hwnd, ctypes.byref(pid))
        pname = ""
        if psutil is not None:
            try:
                pname = psutil.Process(pid.value).name().lower()
            except Exception:
                pass
        if "flet" in pname or "FletMaster" in title:
            found.append(int(hwnd))
        return True

    try:
        ctypes.windll.user32.EnumWindows(enum_proc, 0)
    except Exception:
        return 0
    return found[0] if found else 0


def apply_flet_window_style() -> bool:
    hwnd = find_flet_window()
    if not hwnd:
        return False
    ok = _dwm_set(hwnd, 33, 2)       # DWMWCP_ROUND
    _dwm_set(hwnd, 34, -2)           # 去掉 DWM 浅色描边
    return ok


# ---------------------------------------------------------------------------
# 只读测量与安全清理
# ---------------------------------------------------------------------------
def _safe_size(path: str) -> int:
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += _safe_size(entry.path)
                except (PermissionError, OSError, FileNotFoundError):
                    continue
    except (PermissionError, OSError, FileNotFoundError):
        return 0
    return total


def _browser_cache_size() -> int:
    total = 0
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            profiles = [p for p in root.iterdir()
                        if p.is_dir() and
                        (p.name == "Default" or p.name.startswith("Profile "))]
        except OSError:
            continue
        for profile in profiles:
            for sub in ("Cache", "Code Cache", "GPUCache"):
                p = profile / sub
                if p.is_dir():
                    total += _safe_size(str(p))
    return total


def _thumb_cache_size() -> int:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / \
        "Microsoft" / "Windows" / "Explorer"
    total = 0
    try:
        for p in base.glob("thumbcache_*.db"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def measure_sizes() -> dict[str, int]:
    temp = os.environ.get("TEMP", "")
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    windir = os.environ.get("WINDIR", r"C:\Windows")
    local = os.environ.get("LOCALAPPDATA", "")

    sizes = {
        "temp": _safe_size(str(temp)) + _safe_size(os.path.join(windir, "Temp")),
        "logs": (_safe_size(os.path.join(windir, "Logs")) +
                 _safe_size(os.path.join(program_data, "Microsoft", "Windows", "WER")) +
                 _safe_size(os.path.join(windir, "Minidump"))),
        "browser": _browser_cache_size(),
        "thumb": _thumb_cache_size(),
        "update": _safe_size(os.path.join(windir, "SoftwareDistribution", "Download")),
        "component": 0,
        "recycle": 0,
        "hibernate": 0,
    }
    try:
        hibernate = os.path.join(windir, "hiberfil.sys")
        if os.path.exists(hibernate):
            sizes["hibernate"] = os.path.getsize(hibernate)
    except OSError:
        pass
    try:
        mem_dmp = os.path.join(windir, "MEMORY.DMP")
        if os.path.exists(mem_dmp):
            sizes["logs"] += os.path.getsize(mem_dmp)
    except OSError:
        pass
    return sizes


def _remove_path(path: str, stats: dict[str, int]) -> None:
    try:
        if os.path.islink(path):
            os.unlink(path)
            stats["files"] += 1
            return
        if os.path.isdir(path):
            for child in os.listdir(path):
                _remove_path(os.path.join(path, child), stats)
            try:
                os.rmdir(path)
            except OSError:
                pass
        else:
            os.unlink(path)
            stats["files"] += 1
    except (PermissionError, OSError, FileNotFoundError):
        stats["skipped"] += 1


def clear_directory(path: str) -> dict[str, int]:
    stats = {"files": 0, "skipped": 0}
    if not path or not os.path.isdir(path):
        return stats
    try:
        for child in os.listdir(path):
            _remove_path(os.path.join(path, child), stats)
    except (PermissionError, OSError):
        stats["skipped"] += 1
    return stats


def clean_selected(selected: set[str], admin: bool) -> list[tuple[str, str]]:
    temp = os.environ.get("TEMP", "")
    windir = os.environ.get("WINDIR", r"C:\Windows")
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    local = os.environ.get("LOCALAPPDATA", "")
    results: list[tuple[str, str]] = []

    if "temp" in selected:
        a = clear_directory(temp)
        b = clear_directory(os.path.join(windir, "Temp"))
        n = a["files"] + b["files"]
        s = a["skipped"] + b["skipped"]
        results.append(("ok" if n else "warn",
                        f"Temp 临时文件：删除 {n} 项，跳过 {s} 项"))

    if "logs" in selected:
        if not admin:
            results.append(("warn", "系统日志需要管理员权限，已跳过"))
        else:
            n = s = 0
            for p in (os.path.join(program_data, "Microsoft", "Windows", "WER"),
                      os.path.join(windir, "Minidump")):
                st = clear_directory(p)
                n += st["files"]
                s += st["skipped"]
            results.append(("ok", f"错误报告/转储：删除 {n} 项，跳过 {s} 项"))

    if "browser" in selected:
        n = s = 0
        roots = [
            Path(local) / "Google" / "Chrome" / "User Data",
            Path(local) / "Microsoft" / "Edge" / "User Data",
        ]
        for root in roots:
            if not root.is_dir():
                continue
            for profile in root.iterdir():
                if not profile.is_dir():
                    continue
                if not (profile.name == "Default" or profile.name.startswith("Profile ")):
                    continue
                for sub in ("Cache", "Code Cache", "GPUCache"):
                    st = clear_directory(str(profile / sub))
                    n += st["files"]
                    s += st["skipped"]
        results.append(("ok" if n else "warn",
                        f"浏览器缓存：删除 {n} 项，跳过 {s} 项"))

    if "thumb" in selected:
        base = Path(local) / "Microsoft" / "Windows" / "Explorer"
        n = s = 0
        for p in base.glob("thumbcache_*.db"):
            try:
                p.unlink()
                n += 1
            except OSError:
                s += 1
        results.append(("ok" if n else "warn",
                        f"缩略图缓存：删除 {n} 项，跳过 {s} 项"))

    if "update" in selected:
        results.append(("warn", "更新缓存需要管理员与服务状态备份，已跳过"))
    if "component" in selected:
        results.append(("warn", "废弃组件应通过 DISM 计划处理，已跳过"))
    if "recycle" in selected:
        results.append(("warn", "回收站清理不可恢复，未执行"))
    if "hibernate" in selected:
        results.append(("warn", "关闭休眠属于系统级修改，未执行"))

    return results


# ---------------------------------------------------------------------------
# Flet 应用
# ---------------------------------------------------------------------------
class FletMaster:
    def __init__(self, page: ft.Page):
        self.page = page
        self.admin = is_admin()
        self.scanning = False
        self.is_scanned = False
        self.optimizing = False
        self.page_index = 0
        self.sizes: dict[str, int] = {
            "temp": 0, "logs": 0, "browser": 0, "thumb": 0,
            "update": 0, "component": 0, "recycle": 0, "hibernate": 0,
        }
        self.state: dict[str, bool] = {
            "temp": True, "logs": True, "browser": True, "thumb": True,
            "update": True, "component": False,
            "recycle": False, "hibernate": False,
            "hags": False, "windowed": False, "faststart": False, "sysmain": False,
        }
        self.clean_rows: dict[str, ft.Switch] = {}
        self.nav_items: list[ft.Container] = []
        self.page_views: list[ft.Container] = []
        self.log_view = ft.ListView(expand=True, spacing=2, auto_scroll=True)
        self.metric_value = ft.Text("0.00", size=44, color=TEXT,
                                    weight=ft.FontWeight.BOLD,
                                    font_family="Bahnschrift")
        self.metric_unit = ft.Text("GB", size=13, color=MUTED,
                                   weight=ft.FontWeight.BOLD)
        self.metric_ring = ft.ProgressRing(
            width=42, height=42, stroke_width=2,
            color=ACCENT, bgcolor="#1A3A40",
        )
        self.total_label = ft.Text("0.00 GB", size=13, color=MUTED)
        self.action_label = ft.Text("扫描系统", size=13, color=TEXT,
                                    weight=ft.FontWeight.BOLD)
        self.action_button = ft.Container(
            width=260, height=48, border_radius=8, bgcolor=ACCENT,
            alignment=ft.Alignment.CENTER, ink=True,
            on_click=self.on_optimize,
            content=ft.Row(
                [self.action_label],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self.scan_button = ft.Container(
            height=34, width=96, border_radius=6, bgcolor="#15161A",
            alignment=ft.Alignment.CENTER, ink=True,
            on_click=self.start_scan,
            content=ft.Text("扫描系统", color=BODY, size=12,
                            weight=ft.FontWeight.BOLD),
        )
        self.page_title = ft.Text("深度清理", size=22, color=TEXT,
                                  weight=ft.FontWeight.BOLD)
        self.page_subtitle = ft.Text(
            "只清理可再生缓存与日志；回收站、休眠文件单独列出。",
            size=12, color=MUTED)
        self.stack = ft.Column(expand=True, spacing=0)
        self.dialog_open = False

    # ---- 窗口配置 -----------------------------------------------------
    def configure_window(self) -> None:
        w = self.page.window
        w.frameless = True
        w.title_bar_hidden = True
        w.title_bar_buttons_hidden = True
        w.width = WINDOW_W
        w.height = WINDOW_H
        w.min_width = 1050
        w.min_height = 650
        # w.aspect_ratio = 16 / 10
        w.bgcolor = BG
        w.shadow = False
        w.maximized = False
        w.resizable = True
        w.prevent_close = True
        w.on_event = self._on_window_event
        self.page.bgcolor = BG
        self.page.padding = 0
        self.page.spacing = 0
        self.page.enable_screenshots = True
        try:
            self.page.title = "FletMaster · 深度优化大师"
        except Exception:
            pass

    # ---- 构造 ---------------------------------------------------------
    def build(self) -> None:
        self.configure_window()
        prepare_assets()

        self.titlebar = self._build_titlebar()
        self.sidebar = self._build_sidebar()
        self.content = self._build_content()
        self.about_overlay = self._build_about_overlay()

        base = ft.Container(
            expand=True,
            bgcolor=BG,
            border_radius=RADIUS,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    self.titlebar,
                    ft.Row(
                        [self.sidebar, self.content],
                        expand=True,
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
        )
        root = ft.Stack(
            [base, self.about_overlay],
            expand=True,
            fit=ft.StackFit.EXPAND,
        )
        self.page.add(root)

    def _build_titlebar(self) -> ft.Control:
        logo = ft.Container(
            width=28, height=28, border_radius=8,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=image_or_fallback("logo.jpg", 28, 8),
        )
        brand = ft.Row(
            [
                logo,
                ft.Column(
                    [
                        ft.Text("FletMaster", size=14, color=TEXT,
                                weight=ft.FontWeight.BOLD),
                        ft.Text("极简 · 原生拖拽", size=9, color=DIM),
                    ],
                    spacing=1,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        drag_area = ft.WindowDragArea(
            content=ft.Container(
                content=brand,
                padding=ft.Padding(left=S3, top=0, right=S2, bottom=0),
                alignment=ft.Alignment.CENTER_LEFT,
            ),
            expand=True,
            height=56,
            maximizable=True,
            on_double_tap=self.toggle_maximize,
        )
        buttons = ft.Row(
            [
                self._window_button(ft.Icons.REMOVE, self.minimize),
                self._window_button(ft.Icons.CROP_SQUARE, self.toggle_maximize),
                self._window_button(ft.Icons.CLOSE, self.close),
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.Container(
            height=56,
            bgcolor=BG,
            border=ft.Border(bottom=ft.BorderSide(1, "#15161A")),
            content=ft.Row(
                [drag_area, buttons],
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _window_button(self, icon, handler) -> ft.Control:
        return ft.Container(
            width=46, height=56, alignment=ft.Alignment.CENTER,
            ink=True, on_click=handler,
            content=ft.Icon(icon, size=15, color=MUTED),
        )

    def _nav(self, index: int, icon, label: str) -> ft.Control:
        active = index == self.page_index
        item = ft.Container(
            height=44,
            padding=ft.Padding(left=S2, right=S2, top=0, bottom=0),
            border=ft.Border(
                left=ft.BorderSide(2, ACCENT if active else "transparent")
            ),
            on_click=lambda e, i=index: self.navigate(i),
            content=ft.Row(
                [
                    ft.Icon(icon, size=16,
                            color=ACCENT if active else DIM),
                    ft.Text(label, size=13,
                            color=TEXT if active else MUTED,
                            weight=ft.FontWeight.BOLD if active else ft.FontWeight.NORMAL),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self.nav_items.append(item)
        return item

    def _build_sidebar(self) -> ft.Control:
        self.nav_items.clear()
        nav = ft.Column(
            [
                ft.Text("工作台", size=10, color=DIM,
                        weight=ft.FontWeight.BOLD),
                self._nav(0, ft.Icons.CLEANING_SERVICES, "深度清理"),
                self._nav(1, ft.Icons.BOLT, "系统瘦身"),
                self._nav(2, ft.Icons.TUNE, "服务与组件"),
                ft.Container(height=S3),
                ft.Text("系统状态", size=10, color=DIM,
                        weight=ft.FontWeight.BOLD),
                self._status_panel(),
                ft.Container(expand=True),
            ],
            spacing=4,
            expand=True,
        )

        footer = ft.Container(
            padding=ft.Padding(left=S2, right=S2, top=S2, bottom=S2),
            border=ft.Border(top=ft.BorderSide(1, "#17181C")),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("v1.0", size=10, color=DIM),
                            ft.Container(expand=True),
                            self._footer_icon(ft.Icons.SETTINGS_OUTLINED,
                                              self.show_settings, "设置"),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        height=32, border_radius=6, ink=True,
                        on_click=self.show_about,
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=MUTED),
                                ft.Text("关于作者", size=11, color=MUTED),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ),
                ],
                spacing=2,
            ),
        )

        return ft.Container(
            width=228,
            bgcolor=SIDEBAR_BG,
            border=ft.Border(right=ft.BorderSide(1, "#17181C")),
            padding=ft.Padding(left=S2, right=S2, top=S3, bottom=S2),
            content=ft.Column([nav, footer], spacing=0, expand=True),
        )

    def _footer_icon(self, icon, handler, tooltip: str) -> ft.Control:
        return ft.Container(
            width=30, height=30, border_radius=6, ink=True,
            tooltip=tooltip, on_click=handler,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(icon, size=17, color=MUTED),
        )

    def _status_panel(self) -> ft.Control:
        dot = ft.Container(width=7, height=7, border_radius=4,
                           bgcolor=GREEN if self.admin else RED)
        state = "管理员模式" if self.admin else "普通模式"
        sub = "已提权，系统级操作可用" if self.admin else "服务与注册表操作受限"
        content_children: list[ft.Control] = [
            ft.Row(
                [
                    dot,
                    ft.Text(state, size=12, color=TEXT,
                            weight=ft.FontWeight.BOLD),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(sub, size=10, color=MUTED, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
        ]
        if not self.admin:
            content_children.append(
                ft.TextButton(
                    content=ft.Text("以管理员重启", size=10, color=ACCENT),
                    on_click=self.restart_as_admin,
                )
            )
        return ft.Container(
            padding=ft.Padding(left=S1, top=S2, right=0, bottom=0),
            border=ft.Border(left=ft.BorderSide(2, ACCENT)),
            content=ft.Column(content_children, spacing=4),
        )

    def _build_content(self) -> ft.Control:
        self.page_views.clear()
        self.page_views.extend([
            self._build_clean_page(),
            self._build_tune_page(),
            self._build_service_page(),
        ])
        self.page_views[0].visible = True
        self.stack.controls = self.page_views

        header = ft.Row(
            [
                ft.Column([self.page_title, self.page_subtitle],
                          spacing=4, expand=True),
                self.scan_button,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=S2,
        )

        log_card = ft.Container(
            expand=True,
            height=162,
            bgcolor=PANEL,
            border_radius=RADIUS,
            padding=ft.Padding(S2, S2, S2, S2),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row(
                                [
                                    ft.Container(width=5, height=5,
                                                 border_radius=3,
                                                 bgcolor=ACCENT),
                                    ft.Text("日志", size=11, color=MUTED,
                                            weight=ft.FontWeight.BOLD),
                                ],
                                spacing=8,
                            ),
                            ft.Container(expand=True),
                            ft.Text("本地引擎 · 线程安全", size=10, color=DIM),
                        ],
                    ),
                    self.log_view,
                ],
                spacing=6,
                expand=True,
            ),
        )

        metric_card = ft.Container(
            width=316,
            height=162,
            bgcolor=PANEL,
            border_radius=RADIUS,
            padding=ft.Padding(S3, S2, S3, S2),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("可释放空间", size=11, color=MUTED),
                            ft.Container(expand=True),
                            ft.Text("估算值", size=10, color=DIM),
                        ],
                    ),
                    ft.Row(
                        [
                            self.metric_value,
                            ft.Container(width=4),
                            self.metric_unit,
                            ft.Container(expand=True),
                            self.metric_ring,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        spacing=0,
                    ),
                    self.action_button,
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

        content = ft.Container(
            expand=True,
            gradient=ft.RadialGradient(
                colors=["#141518", BG],
                stops=[0.0, 1.0],
                center=ft.Alignment.CENTER,
                radius=1.0,
            ),
            padding=ft.Padding(S4, S3, S4, S2),
            content=ft.Column(
                [
                    header,
                    self.stack,
                    ft.Row(
                        [log_card, metric_card],
                        spacing=S2,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=S2,
                expand=True,
            ),
        )
        # 底部日志/指标卡片固定，stack 占剩余高度
        content.content.controls[1].expand = True
        return content

    # ---- 页面 ---------------------------------------------------------
    def _page_shell(self, title: str, desc: str,
                    rows: list[ft.Control]) -> ft.Container:
        return ft.Container(
            visible=False, expand=True, padding=0,
            content=ft.Column(
                [
                    ft.Container(height=S2),
                    ft.Column(rows, spacing=0, scroll=ft.ScrollMode.AUTO,
                              expand=True),
                ],
                spacing=0, expand=True,
            ),
        )

    def _feature_row(self, key: str, title: str, desc: str,
                     risk: str, accent: str = GREEN,
                     checked: bool = True, enabled: bool = True,
                     switch_ref: bool = False) -> ft.Control:
        sw = ft.Switch(
            value=checked,
            active_color=ACCENT,
            active_track_color="#123A42",
            inactive_track_color="#25272D",
            thumb_color="#F8FAFC",
            width=44, height=24,
            disabled=not enabled,
        )
        if switch_ref:
            self.clean_rows[key] = sw
        sw.on_change = lambda e, k=key, s=sw: self._toggle(k, e)

        def hover(e):
            e.control.bgcolor = ROW_HOVER if e.data == "true" else None
            e.control.update()

        return ft.Container(
            height=72,
            padding=ft.Padding(0, 0, 0, 0),
            border=ft.Border(bottom=ft.BorderSide(1, "#191A1E")),
            on_hover=hover,
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(title, size=14, color=TEXT,
                                            weight=ft.FontWeight.BOLD),
                                    ft.Text(f"· {risk}", size=10,
                                            color=accent),
                                ],
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Text(desc, size=11, color=MUTED,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                        ],
                        spacing=3,
                        expand=True,
                    ),
                    sw,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=S2,
            ),
        )

    def _build_clean_page(self) -> ft.Container:
        rows = [
            self._feature_row("temp", "Temp 临时文件",
                              "清理 %TEMP%、Windows\\Temp 与安装残留；被占用文件自动跳过。",
                              "安全", GREEN, True, True, True),
            self._feature_row("logs", "系统日志与错误报告",
                              "Windows\\Logs、WER、Minidump 与 MEMORY.DMP；正在排查蓝屏时保留。",
                              "安全", GREEN, True, True, True),
            self._feature_row("browser", "浏览器缓存",
                              "Edge / Chrome 各配置的 Cache、Code Cache、GPUCache；不碰历史与密码。",
                              "安全", GREEN, True, True, True),
            self._feature_row("thumb", "缩略图缓存",
                              "Explorer thumbcache_*；删除后系统会自动重建，不碰任何个人图片。",
                              "安全", GREEN, True, True, True),
            self._feature_row("update", "Windows 更新残留",
                              "SoftwareDistribution\\Download；服务状态需单独确认，当前仅计划。",
                              "谨慎", AMBER, True, True, True),
            self._feature_row("component", "废弃组件 / WinSxS",
                              "组件存储只能用 DISM StartComponentCleanup；当前仅展示计划，不直接删除。",
                              "谨慎", AMBER, False, True, True),
            self._feature_row("recycle", "回收站",
                              "清空后不可恢复；扫描只报告，默认关闭，需单独确认。",
                              "不可逆", RED, False, True, True),
            self._feature_row("hibernate", "关闭休眠文件",
                              "powercfg -h off 释放约等于内存大小的空间；属于系统级修改，默认关闭。",
                              "谨慎", AMBER, False, True, True),
        ]
        return self._page_shell(
            "深度清理",
            "只清理可再生缓存与日志；个人桌面、文档、图片不会进入清理队列。",
            rows,
        )

    def _build_tune_page(self) -> ft.Container:
        rows = [
            self._feature_row("hags", "硬件加速 GPU 计划",
                              "HAGS 可改善部分游戏帧生成；需重启，当前仅生成计划。",
                              "谨慎", AMBER, False, True),
            self._feature_row("windowed", "窗口化游戏优化",
                              "窗口化游戏优化可降低部分场景延迟；需显卡驱动支持。",
                              "安全", GREEN, False, True),
            self._feature_row("faststart", "快速启动",
                              "缩短冷启动时间；会改变关机行为，当前仅生成计划。",
                              "谨慎", AMBER, False, True),
            self._feature_row("sysmain", "SysMain / 超级预读",
                              "机械硬盘收益更明显；SSD 可按需关闭，当前仅生成计划。",
                              "谨慎", AMBER, False, True),
        ]
        return self._page_shell(
            "系统瘦身",
            "只展示可调项与风险等级；Flet 版不直接修改注册表或服务。",
            rows,
        )

    def _build_service_page(self) -> ft.Container:
        self.service_list = ft.Column([], spacing=0, scroll=ft.ScrollMode.AUTO,
                                      expand=True)
        self.service_count = ft.Text("等待扫描", size=11, color=MUTED)
        button = ft.Container(
            height=32, width=108, border_radius=6, bgcolor="#15161A",
            alignment=ft.Alignment.CENTER, ink=True,
            on_click=lambda e: self.page.run_task(self.scan_services),
            content=ft.Text("只读扫描", size=11, color=BODY,
                            weight=ft.FontWeight.BOLD),
        )
        shell = self._page_shell(
            "服务与组件",
            "只读查看第三方服务；本页不会停止、禁用或删除任何服务。",
            [
                ft.Row([self.service_count, ft.Container(expand=True), button],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=S2),
                self.service_list,
            ],
        )
        return shell

    # ---- 导航与窗口操作 -----------------------------------------------
    def navigate(self, index: int) -> None:
        if index < 0 or index >= len(self.page_views):
            return
        self.page_index = index
        for i, item in enumerate(self.nav_items):
            active = i == index
            item.border = ft.Border(
                left=ft.BorderSide(2, ACCENT if active else "transparent"))
            row = item.content
            row.controls[0].color = ACCENT if active else DIM
            row.controls[1].color = TEXT if active else MUTED
            row.controls[1].weight = (
                ft.FontWeight.BOLD if active else ft.FontWeight.NORMAL)
        for i, view in enumerate(self.page_views):
            view.visible = i == index
        titles = [
            ("深度清理", "只清理可再生缓存与日志；个人桌面、文档、图片不会进入清理队列。"),
            ("系统瘦身", "只展示可调项与风险等级；Flet 版不直接修改注册表或服务。"),
            ("服务与组件", "只读查看第三方服务；不会停止、禁用或删除任何服务。"),
        ]
        self.page_title.value, self.page_subtitle.value = titles[index]
        self.page.update()

    def minimize(self, e=None) -> None:
        self.page.window.minimized = True
        self.page.update()

    def toggle_maximize(self, e=None) -> None:
        self.page.window.maximized = not self.page.window.maximized
        self.page.update()

    def close(self, e=None) -> None:
        # 不走 page.window.close，直接杀 Flet 子进程后 os._exit(0)
        hard_exit()

    def _on_window_event(self, e: ft.WindowEvent) -> None:
        if e.type == ft.WindowEventType.CLOSE:
            hard_exit()

    # ---- 日志 ---------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        color = {"ok": GREEN, "info": MUTED, "warn": AMBER, "error": RED}.get(level, MUTED)
        self.log_view.controls.append(
            ft.Text(f"[{fmt_time()}] {message}", size=11, color=color,
                    font_family="Microsoft YaHei UI")
        )
        if len(self.log_view.controls) > 120:
            del self.log_view.controls[:20]
        try:
            self.log_view.update()
        except Exception:
            pass

    # ---- 开关 ---------------------------------------------------------
    def _toggle(self, key: str, e) -> None:
        self.state[key] = bool(e.control.value)
        self._refresh_total()
        self.page.update()

    def _refresh_total(self) -> None:
        selected_keys = {"temp", "logs", "browser", "thumb", "update",
                         "component", "recycle", "hibernate"}
        total = sum(self.sizes.get(k, 0) for k in selected_keys if self.state.get(k))
        gb = human_gb(total)
        self.metric_value.value = f"{gb:.2f}"
        self.total_label.value = human_size(total)
        self.action_label.value = f"一键优化（释放 {gb:.2f}GB）"
        self.metric_value.update()
        self.action_label.update()
        if hasattr(self, "total_label"):
            try:
                self.total_label.update()
            except Exception:
                pass

    # ---- 扫描 ---------------------------------------------------------
    def start_scan(self, e=None) -> None:
        if self.scanning:
            return
        self.page.run_task(self.scan_async)

    async def scan_async(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.is_scanned = False
        self.scan_button.content = ft.Text("扫描中…", size=12, color=ACCENT)
        self.action_label.value = "扫描中…"
        self.action_button.bgcolor = "#4B5563"
        self.action_button.disabled = True
        self.log("开始只读扫描；不会修改任何文件。", "info")
        self.page.update()
        try:
            sizes = await asyncio.to_thread(measure_sizes)
            self.sizes = sizes
            # 只有扫描成功后才置 True；finally 中绝不重置
            self.is_scanned = True
            self._refresh_total()
            self.log(
                "扫描完成：Temp {0}，浏览器 {1}，日志 {2}，更新残留 {3}。".format(
                    human_size(sizes["temp"]), human_size(sizes["browser"]),
                    human_size(sizes["logs"]), human_size(sizes["update"]), ),
                "ok")
        except Exception as exc:
            self.log(f"扫描失败：{exc}", "error")
        finally:
            self.scanning = False
            self.scan_button.content = ft.Text("重新扫描", size=12, color=BODY)
            self.action_label.value = (
                f"一键优化（释放 {human_gb(sum(self.sizes[k] for k in self.sizes if self.state.get(k)))}GB）"
                if not self.optimizing else "优化中…"
            )
            self.action_button.bgcolor = ACCENT
            self.action_button.disabled = False
            self.page.update()

    # ---- 优化 ---------------------------------------------------------
    def on_optimize(self, e=None) -> None:
        if self.optimizing or self.scanning:
            return
        selected = {k for k in ("temp", "logs", "browser", "thumb",
                                "update", "component", "recycle", "hibernate")
                    if self.state.get(k)}
        if not selected:
            self.log("没有勾选任何清理项。", "warn")
            return
        total = sum(self.sizes.get(k, 0) for k in selected)
        content = ft.Column(
            [
                ft.Text("将执行已勾选的安全项；被占用文件自动跳过。",
                        size=12, color=BODY),
                ft.Text("个人文件、桌面、文档、图片不会进入队列。",
                        size=12, color=MUTED),
                ft.Text(f"预计可释放：{human_size(total)}", size=13,
                        color=ACCENT, weight=ft.FontWeight.BOLD),
                ft.Text("回收站 / 关闭休眠 / 更新服务属于单独确认项，本次不会执行。",
                        size=11, color=AMBER),
            ],
            spacing=8, tight=True,
        )
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=BG,
            title=ft.Text("确认清理", size=16, color=TEXT,
                          weight=ft.FontWeight.BOLD),
            content=ft.Container(content, width=380),
            actions=[
                ft.TextButton(
                    content=ft.Text("取消", color=MUTED),
                    on_click=lambda e: self.page.pop_dialog()),
                ft.TextButton(
                    content=ft.Text("开始清理", color=ACCENT),
                    on_click=lambda e: self.confirm_optimize(dialog),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    def confirm_optimize(self, dialog) -> None:
        self.page.pop_dialog()
        self.page.run_task(self.optimize_async)

    async def optimize_async(self) -> None:
        if self.optimizing:
            return
        selected = {k for k in ("temp", "logs", "browser", "thumb",
                                "update", "component", "recycle", "hibernate")
                    if self.state.get(k)}
        self.optimizing = True
        self.action_button.bgcolor = "#4B5563"
        self.action_button.disabled = True
        self.action_label.value = "优化中…"
        self.log("开始清理已勾选的安全项。", "info")
        self.page.update()
        try:
            results = await asyncio.to_thread(
                clean_selected, selected, self.admin)
            for level, message in results:
                self.log(message, level)
            self.log("清理结束，开始重新扫描。", "ok")
            self.optimizing = False
            await self.scan_async()
        except Exception as exc:
            self.log(f"清理失败：{exc}", "error")
        finally:
            self.optimizing = False
            self.action_button.bgcolor = ACCENT
            self.action_button.disabled = False
            self.page.update()

    # ---- 服务只读扫描 -------------------------------------------------
    async def scan_services(self) -> None:
        if psutil is None:
            self.log("psutil 不可用，无法读取服务列表。", "warn")
            return
        self.service_count.value = "扫描中…"
        self.page.update()

        def collect():
            items = []
            for svc in psutil.win_service_iter():
                try:
                    info = svc.as_dict()
                except Exception:
                    continue
                if info.get("display_name", "").lower().startswith("windows"):
                    continue
                items.append((info.get("display_name") or info.get("name"),
                              info.get("status", "unknown")))
            return items[:24]

        try:
            items = await asyncio.to_thread(collect)
            self.service_list.controls.clear()
            for name, status in items:
                self.service_list.controls.append(
                    ft.Container(
                        height=48,
                        border=ft.Border(bottom=ft.BorderSide(1, "#191A1E")),
                        content=ft.Row(
                            [
                                ft.Text(name, size=12, color=BODY,
                                        expand=True, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(status, size=10, color=DIM),
                            ],
                            spacing=S2,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )
            self.service_count.value = f"只读 · {len(items)} 项"
            self.log(f"服务只读扫描完成：{len(items)} 项。", "ok")
        except Exception as exc:
            self.service_count.value = "扫描失败"
            self.log(f"服务扫描失败：{exc}", "error")
        self.page.update()

    # ---- 设置 / 关于 ---------------------------------------------------
    def show_settings(self, e=None) -> None:
        dialog = ft.AlertDialog(
            modal=True, bgcolor=BG,
            title=ft.Text("设置", size=16, color=TEXT,
                          weight=ft.FontWeight.BOLD),
            content=ft.Container(
                ft.Column([
                    ft.Text("启动时自动扫描", size=12, color=BODY),
                    ft.Text("FletMaster 启动后立即读取可释放空间。",
                            size=11, color=MUTED),
                ], spacing=8), width=320),
            actions=[
                ft.TextButton(content=ft.Text("关闭", color=ACCENT),
                              on_click=lambda e: self.page.pop_dialog()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    def _build_about_overlay(self) -> ft.Control:
        avatar = ft.Container(
            width=88, height=88, border_radius=44,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=ft.BoxShadow(blur_radius=30, color="#4400E5FF",
                                offset=ft.Offset(0, 0)),
            content=image_or_fallback("avatar.jpg", 88, 44),
        )
        card = ft.Container(
            width=400,
            bgcolor=BG,
            border_radius=14,
            padding=ft.Padding(S4, S3, S4, S2),
            shadow=ft.BoxShadow(blur_radius=28, color="#55000000",
                                offset=ft.Offset(0, 10)),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE, icon_size=16,
                                icon_color=MUTED, tooltip="关闭",
                                on_click=lambda e: self.hide_about(),
                            ),
                        ]
                    ),
                    ft.Row([avatar], alignment=ft.MainAxisAlignment.CENTER),
                    ft.Text(AUTHOR_NAME, size=21, color=TEXT,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER),
                    ft.Text("Windows 深度清理爱好者", size=11, color=MUTED,
                            text_align=ft.TextAlign.CENTER),
                    ft.Container(height=S2),
                    ft.TextButton(
                        content=ft.Text("打开抖音主页", size=12, color=ACCENT),
                        url=AUTHOR_URL,
                    ),
                    ft.Container(height=S1),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        return ft.Container(
            visible=False,
            expand=True,
            bgcolor="#B3000000",
            alignment=ft.Alignment.CENTER,
            content=card,
        )

    def show_about(self, e=None) -> None:
        self.about_overlay.visible = True
        self.page.update()

    def hide_about(self) -> None:
        self.about_overlay.visible = False
        self.page.update()

    # ---- 管理员重启 --------------------------------------------------
    def restart_as_admin(self, e=None) -> None:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = ""
            cwd = str(Path(sys.executable).resolve().parent)
        else:
            script = Path(__file__).resolve()
            exe = sys.executable
            params = f'"{script}"'
            cwd = str(script.parent)

        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell32.ShellExecuteW.restype = ctypes.c_ulonglong

        try:
            ret = shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
            if int(ret) > 32:
                self.log(
                    "已请求管理员重启；旧进程将在 1 秒后强制退出。",
                    "info",
                )
                threading.Timer(1.0, hard_exit).start()
            else:
                self.log("管理员重启已取消或被系统拒绝。", "warn")
        except Exception as exc:
            self.log(f"提权失败：{exc}", "error")

    # ---- 启动 ---------------------------------------------------------
    def _boot(self) -> None:
        self.page.run_task(self._boot_async)

    async def _boot_async(self) -> None:
        self.log("Flet 原生拖拽区域已启用；标题栏不再使用 Python 逐帧移动。", "ok")
        self.log("本地引擎已启动；清理操作全部在线程池执行。", "info")
        try:
            await self.page.window.center()
        except Exception:
            pass
        self.page.run_task(self._bring_to_front)
        await asyncio.sleep(0.2)
        applied = False
        for _ in range(3):
            if apply_flet_window_style():
                applied = True
                break
            await asyncio.sleep(0.4)
        if applied:
            self.log("Windows DWM 原生圆角已应用。", "ok")
        else:
            self.log("DWM 原生窗口样式暂不可用。", "warn")
        await self.scan_async()
        if os.environ.get("FLETMASTER_ABOUT"):
            self.show_about()
        if os.environ.get("FLETMASTER_SCREENSHOT"):
            try:
                await asyncio.sleep(0.35)
                png = await self.page.take_screenshot()
                Path(__file__).resolve().parent.joinpath(
                    "FletMaster_runtime.png").write_bytes(png)
                self.log("运行截图已保存。", "ok")
            except Exception as exc:
                self.log(f"截图失败：{exc}", "warn")

    async def _bring_to_front(self) -> None:
        try:
            await asyncio.wait_for(self.page.window.to_front(), timeout=1.0)
        except Exception:
            pass


def main(page: ft.Page) -> None:
    app = FletMaster(page)
    app.build()
    app._boot()


if __name__ == "__main__":
    prepare_assets()

    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        os._exit(0)

    try:
        ft.run(main, assets_dir=str(ASSET_DIR))
    finally:
        instance_guard.close()
        hard_exit()
