"""MITOM launcher desktop view.

This module owns presentation only. The host supplies action callbacks and reports
state through ``set_state``, ``set_busy``, ``set_progress`` and ``log``. Those four
methods may be called from worker threads; Tk calls run on the UI thread.

Run ``python mitom_ui.py --demo`` for a read-only visual preview.
"""
from __future__ import annotations

import argparse
import ctypes
import platform
import queue
import sys
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:  # The launcher still functions without optional image scaling.
    Image = ImageTk = None


ASSETS = Path(__file__).resolve().parent / "assets"
BG = "#f2f1ed"
PAPER = "#faf9f5"
INK = "#151516"
MUTED = "#62655f"
LINE = "#d2d1cc"
BLUE = "#2466d8"
PALE_BLUE = "#e9eef7"
GREEN = "#39816b"
CAPTION_FONT_FAMILY = "SVN-Helvetica Neue"
CAPTION_TRACKING_SEPARATOR = ""
ORANGE = "#d56b14"


def catalogue_entries(state):
    """Keep installation, counts, filtering and sorting tied to real app state."""
    return [
        {"id": "panel", "name": "Photoshop Panel", "installed": bool(state.get("installed")),
         "update": bool(state.get("installed") and state.get("update_available")),
         "version": state.get("installed_version") or state.get("latest_version", ""),
         "search": "photoshop panel adobe creative workflow order base 3d"},
        {"id": "vector", "name": "Vector Migic", "installed": bool(state.get("vector_installed")),
         "update": bool(state.get("vector_installed") and state.get("vector_update_available")),
         "version": state.get("vector_version") or state.get("vector_latest_version", ""),
         "search": "vector migic vectorize trace raster bitmap svg png jpg jpeg offline cpu"},
    ]


def catalogue_selection(state, filter_key="all", query="", sort="Suite order"):
    entries = catalogue_entries(state)
    counts = {"all": len(entries), "installed": sum(item["installed"] for item in entries),
              "updates": sum(item["update"] for item in entries),
              "available": sum(not item["installed"] for item in entries)}
    query = str(query).strip().lower()
    visible = [item for item in entries if query in item["search"] and
               (filter_key == "all" or filter_key == "installed" and item["installed"] or
                filter_key == "updates" and item["update"] or
                filter_key == "available" and not item["installed"])]
    if sort == "Name A–Z":
        visible.sort(key=lambda item: item["name"].casefold())
    elif sort == "Recently updated":
        visible.sort(key=lambda item: str(item["version"]), reverse=True)
    return counts, visible


_VI = {
    "ALL": "TẤT CẢ", "INSTALLED": "ĐÃ CÀI", "UPDATES": "CẬP NHẬT", "NOT INSTALLED": "CHƯA CÀI",
    "AVAILABLE TO INSTALL": "CÓ THỂ CÀI ĐẶT", "INSTALLED · UP TO DATE": "ĐÃ CÀI · MỚI NHẤT",
    "UPDATE AVAILABLE": "CÓ BẢN CẬP NHẬT", "NOT INCLUDED": "CHƯA CÓ GÓI CÀI",
    "INSTALL VECTOR MIGIC": "CÀI VECTOR MIGIC", "OPEN VECTOR MIGIC": "MỞ VECTOR MIGIC",
    "UPDATE VECTOR MIGIC": "CẬP NHẬT VECTOR MIGIC", "INSTALL PANEL": "CÀI PANEL",
    "REPAIR VECTOR MIGIC": "SỬA VECTOR MIGIC", "REPAIR REQUIRED": "CẦN SỬA CÀI ĐẶT",
    "UPDATE PANEL": "CẬP NHẬT PANEL", "OPEN PHOTOSHOP": "MỞ PHOTOSHOP",
    "GRID": "LƯỚI", "TABLE": "BẢNG", "SEARCH ": "TÌM KIẾM ", "SORT ": "SẮP XẾP ",
    "01 / CREATIVE TOOLS": "01 / CÔNG CỤ SÁNG TẠO", "APPLICATION": "ỨNG DỤNG",
    "STATUS / VERSION / ACTION": "TRẠNG THÁI / PHIÊN BẢN / THAO TÁC",
    "CREATIVE APP SUITE": "BỘ ỨNG DỤNG SÁNG TẠO", "CHECK INSTALLATION ↗": "KIỂM TRA CÀI ĐẶT ↗",
    "Your everyday creative workflow": "Công cụ sáng tạo hàng ngày", "For Adobe Photoshop®": "Dành cho Adobe Photoshop®",
    "Raster to vector. On your computer.": "Chuyển ảnh thành vector trên máy của bạn.",
    "PNG / JPG → SVG · Offline CPU": "PNG / JPG → SVG · CPU ngoại tuyến",
    "COLOR / BLACK & WHITE · SVG": "MÀU / ĐEN TRẮNG · SVG",
    "No apps in this view": "Không có ứng dụng trong mục này",
    "Try another search or show all apps.": "Thử tìm kiếm khác hoặc hiện tất cả ứng dụng.",
    "SHOW ALL APPS": "HIỆN TẤT CẢ ỨNG DỤNG", "READY": "SẴN SÀNG", "UPDATE": "CẬP NHẬT",
    "CHECK FOR UPDATES": "KIỂM TRA CẬP NHẬT", "UPDATE LAUNCHER": "CẬP NHẬT LAUNCHER",
    "UPDATE & RESTART": "CẬP NHẬT & KHỞI ĐỘNG LẠI",
    "Settings & installation": "Cài đặt & vị trí cài", "LANGUAGE": "NGÔN NGỮ",
    "Not installed": "Chưa cài", "Available after installation": "Có sau khi cài đặt",
    "INSTALL LOCATION": "VỊ TRÍ CÀI ĐẶT", "DOWNLOAD CACHE": "BỘ NHỚ TẢI XUỐNG",
    "CODE SIGNING": "CHỮ KÝ PHẦN MỀM", "CLOSE": "ĐÓNG", "OPEN CACHE": "MỞ BỘ NHỚ TẢI",
    "Personal login and machine settings stay on this computer.": "Đăng nhập và cấu hình riêng được giữ trên máy này.",
    "Website ↗": "Trang web ↗", "Repair / reinstall Vector Migic": "Sửa / cài lại Vector Migic",
    "Photoshop Panel is installed. Your creative tools are ready.": "Photoshop Panel đã được cài. Công cụ sáng tạo đã sẵn sàng.",
    "Your creative workflow starts with Photoshop Panel.": "Bắt đầu sáng tạo với Photoshop Panel.",
    "Desktop interface preview · installation actions are disabled.": "Xem trước giao diện · chức năng cài đặt đang tắt.",
    "Ready. Choose an app to install or open.": "Sẵn sàng. Chọn ứng dụng để cài hoặc mở.",
}


def _spaced_caption(value):
    """Preserve natural font spacing and the original caption text."""
    return str(value)


class _SpacedCaption:
    """Keep the caption widget API stable while preserving natural spacing."""

    def __init__(self, *args, **kwargs):
        if "text" in kwargs:
            kwargs["text"] = _spaced_caption(kwargs["text"])
        super().__init__(*args, **kwargs)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict) and "text" in cnf:
            cnf = {**cnf, "text": _spaced_caption(cnf["text"])}
        if "text" in kwargs:
            kwargs["text"] = _spaced_caption(kwargs["text"])
        return super().configure(cnf, **kwargs)

    config = configure


class _CaptionLabel(_SpacedCaption, tk.Label):
    pass


class _CaptionButton(_SpacedCaption, tk.Button):
    pass


def _register_private_fonts():
    """Register supplied fonts for this process, never install them system-wide."""
    if sys.platform == "win32":
        for path in ASSETS.glob("*.ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)


class MITOMLauncherUI:
    """A catalogue of actual apps with independently verified install state.

    Action keys: install, verify, check_updates, install_update, open_photoshop,
    companion, chrome, website, rollback, github. Optional action keys:
    update_launcher, open_cache, clear_cache, save_settings. Callbacks receive no arguments,
    except save_settings which receives a dict. No background work is started
    by the view, so the backend controls transaction locking and error handling.

    State keys: installed, installed_version, latest_version, launcher_version,
    update_available, launcher_update_available, launcher_latest_version,
    status, checked_at, website, github, installed_path, cache_path,
    signature_status, preview. Unknown keys are retained for backend metadata.
    """

    def __init__(self, root, actions, initial_state=None):
        self.root = root
        self.actions = dict(actions or {})
        self.state = {
            "installed": False, "installed_version": "", "latest_version": "",
            "launcher_version": "", "update_available": False,
            "launcher_update_available": False, "launcher_latest_version": "",
            "status": "Ready", "checked_at": "Not checked yet",
            "website": "https://mitomyhy.github.io/",
            "github": "https://github.com/Mitomyhy", "preview": False,
            "vector_installed": False, "vector_available": False, "language": "en",
        }
        if initial_state:
            self.state.update(initial_state)
        self._events = queue.Queue()
        self._alive = True
        self._busy = False
        self._busy_text = ""
        self._progress = 0
        self._logs_open = False
        self._log_count = 0
        self._filter = "all"
        self._layout = "grid"
        self._images = []
        self._action_buttons = []
        self._settings = None
        self._language_widgets = []
        _register_private_fonts()
        self._make_fonts()
        self.root.title("MITOM Launcher")
        self.root.configure(bg=BG)
        self.root.minsize(1000, 700)
        self.root.geometry("1120x760")
        try:
            self.root.iconbitmap(str(ASSETS / "favicon.ico"))
        except tk.TclError:
            pass
        self._make_styles()
        self._build()
        self._render()
        self.root.bind("<Control-f>", lambda _e: self.search_entry.focus_set())
        self.root.bind("<F5>", lambda _e: self._invoke("check_updates"))
        self.root.bind("<Escape>", self._escape)
        self.root.bind("<Destroy>", self._on_destroy, add=True)
        self.root.after(40, self._drain_events)

    def _make_fonts(self):
        families = set(tkfont.families(self.root))
        title = next((name for name in ("Akzidenz-Grotesk", "Akzidenz Grotesk", "Akzidenz-Grotesk BQ", "AkzidenzGrotesk") if name in families), None)
        if title is None:
            title = next((name for name in families if "akzidenz" in name.lower()), "Arial")
        # Keep the supplied font private to the launcher, so the downloaded app
        # looks the same on machines that have never installed this font.
        mono = CAPTION_FONT_FAMILY if CAPTION_FONT_FAMILY in families else "Segoe UI"
        self.fonts = {
            "brand": (title, 23, "bold"), "title": (title, 24, "bold"),
            "app": (title, 20, "bold"), "heading": (title, 13, "bold"),
            "small": (mono, 12), "mono": (mono, 12),
            "button": (mono, 12), "icon": ("Segoe UI Symbol", 17, "bold"),
            "body": ("Segoe UI", 11), "body_small": ("Segoe UI", 11),
        }

    def _make_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Mitom.Horizontal.TProgressbar", background=BLUE,
                        troughcolor=LINE, borderwidth=0, lightcolor=BLUE, darkcolor=BLUE)
        style.configure("Mitom.TCombobox", fieldbackground=BG, background=BG,
                        foreground=INK, bordercolor=LINE, arrowcolor=INK,
                        padding=6, font=self.fonts["body_small"])
        style.map("Mitom.TCombobox", fieldbackground=[("readonly", BG)],
                  selectbackground=[("readonly", BG)], selectforeground=[("readonly", INK)])

    def _label(self, parent, text="", kind="body", fg=INK, bg=BG, **kwargs):
        label_type = _CaptionLabel if kind in ("small", "mono", "button") else tk.Label
        widget = label_type(parent, text=self._t(text), font=self.fonts[kind], fg=fg, bg=bg,
                        borderwidth=0, anchor="w", **kwargs)
        if text in _VI:
            self._language_widgets.append((widget, text))
        return widget

    def _t(self, text):
        return _VI.get(text, text) if self.state.get("language") == "vi" else text

    def _button(self, parent, text, command, primary=False, compact=False, **kwargs):
        button = _CaptionButton(parent, text=self._t(text), command=command, cursor="hand2",
                           font=self.fonts["button"], fg=PAPER if primary else INK,
                           bg=INK if primary else BG, activebackground=BLUE if primary else "#e5e4df",
                           activeforeground=PAPER if primary else INK,
                           relief="flat", borderwidth=0, highlightthickness=1,
                           highlightbackground=INK if primary else LINE,
                           padx=11 if compact else 18, pady=7 if compact else 11,
                           disabledforeground="#969792", takefocus=True, **kwargs)
        if text in _VI:
            self._language_widgets.append((button, text))
        return button

    def _action_button(self, parent, text, key, **kwargs):
        button = self._button(parent, text, lambda: self._invoke(key), **kwargs)
        self._action_buttons.append((button, key))
        return button

    def _line(self, parent):
        return tk.Frame(parent, bg=LINE, height=1)

    def _build(self):
        self.header = tk.Frame(self.root, bg=BG, height=72)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        brand = tk.Frame(self.header, bg=BG, padx=20)
        brand.pack(side="left", fill="y")
        self._label(brand, "MITOM", "brand").pack(expand=True)
        tk.Frame(self.header, width=1, bg=LINE).pack(side="left", fill="y")
        self._label(self.header, " LAUNCHER ", "small", fg=PAPER, bg=INK,
                    padx=6, pady=5).pack(side="left", padx=(18, 10))
        self._label(self.header, "CREATIVE APP SUITE", "small", fg=MUTED).pack(side="left")
        settings_button = self._button(self.header, "⚙", self._open_settings, compact=True, width=2)
        settings_button.configure(font=self.fonts["icon"])
        settings_button.pack(side="right", fill="y")
        self.refresh_button = self._action_button(self.header, "↻", "check_updates", compact=True, width=2)
        self.refresh_button.configure(font=self.fonts["icon"])
        self.refresh_button.pack(side="right", fill="y")
        self._action_button(self.header, "GITHUB ↗", "github", compact=True).pack(side="right", fill="y")
        viewbox = tk.Frame(self.header, bg=BG, padx=14)
        viewbox.pack(side="right", fill="y")
        self.table_button = self._button(viewbox, "TABLE", lambda: self._set_layout("table"), compact=True)
        self.table_button.pack(side="right", pady=12)
        self.grid_button = self._button(viewbox, "GRID", lambda: self._set_layout("grid"), primary=True, compact=True)
        self.grid_button.pack(side="right", pady=12)
        self._line(self.root).pack(fill="x")

        self.body = tk.Frame(self.root, bg=BG, padx=24, pady=20)
        self.body.pack(fill="both", expand=True)
        self.banner = tk.Frame(self.body, bg=PALE_BLUE, highlightbackground="#a5bde5", highlightthickness=1, height=72)
        self.banner.pack(fill="x", pady=(0, 19))
        self.banner.pack_propagate(False)
        self.banner_badge = self._label(self.banner, "READY", "small", fg="white", bg=BLUE, padx=8, pady=6)
        self.banner_badge.pack(side="left", padx=(14, 12))
        self.banner_text = self._label(self.banner, "", "body", bg=PALE_BLUE, wraplength=580, justify="left")
        self.banner_text.pack(side="left", fill="x", expand=True)
        self.banner_button = self._button(self.banner, "INSTALL PANEL", self._banner_action, primary=True, compact=True)
        self.banner_button.pack(side="right", padx=12, pady=10)

        tools = tk.Frame(self.body, bg=BG)
        tools.pack(fill="x")
        filterbar = tk.Frame(tools, bg=BG)
        filterbar.pack(fill="x")
        self.filter_buttons = {}
        for key, title in (("all", "ALL"), ("installed", "INSTALLED"), ("updates", "UPDATES"), ("available", "NOT INSTALLED")):
            button = self._button(filterbar, title, lambda k=key: self._set_filter(k), compact=True)
            button.pack(side="left")
            self.filter_buttons[key] = button
        querybar = tk.Frame(tools, bg=BG)
        querybar.pack(fill="x", pady=(12, 0))
        self.search = tk.StringVar()
        self.search_entry = tk.Entry(querybar, textvariable=self.search, relief="flat", bg="#eae9e4",
                                     fg=INK, insertbackground=INK, font=self.fonts["body_small"],
                                     highlightthickness=1, highlightbackground=LINE, highlightcolor=BLUE, width=19)
        self.search_entry.pack(side="right", ipady=8)
        self.search.trace_add("write", lambda *_: self._render_catalogue())
        self._label(querybar, "SEARCH ", "small", fg=MUTED).pack(side="right", padx=(12, 3))
        self.sort = tk.StringVar(value="Suite order")
        self._label(querybar, "SORT ", "small", fg=MUTED).pack(side="left", padx=(0, 8))
        sort = ttk.Combobox(querybar, textvariable=self.sort, values=("Suite order", "Name A–Z", "Recently updated"),
                            state="readonly", width=16, style="Mitom.TCombobox")
        sort.pack(side="left")
        sort.bind("<<ComboboxSelected>>", lambda _e: self._render_catalogue())

        section = tk.Frame(self.body, bg=BG)
        section.pack(fill="x", pady=(24, 13))
        self._label(section, "01 / CREATIVE TOOLS", "small", fg=MUTED).pack(side="left")
        self.app_count = self._label(section, "1 APP", "small", fg=MUTED)
        self.app_count.pack(side="right")

        self.catalogue_host = tk.Frame(self.body, bg=BG)
        self.catalogue_host.pack(fill="both", expand=True)
        self.catalogue_canvas = tk.Canvas(self.catalogue_host, bg=BG, borderwidth=0,
                                         highlightthickness=0, height=365)
        self.catalogue_canvas.pack(side="left", fill="both", expand=True)
        self.catalogue_scrollbar = ttk.Scrollbar(self.catalogue_host, orient="vertical",
                                                 command=self.catalogue_canvas.yview)
        self.catalogue_canvas.configure(yscrollcommand=self.catalogue_scrollbar.set)
        self.catalogue = tk.Frame(self.catalogue_canvas, bg=BG)
        self.catalogue_window = self.catalogue_canvas.create_window(0, 0, anchor="nw", window=self.catalogue)
        self.catalogue_canvas.bind("<Configure>", self._size_catalogue)
        self.catalogue.bind("<Configure>", self._size_catalogue)
        self.root.bind("<MouseWheel>", self._scroll_catalogue, add=True)
        self.catalogue.columnconfigure(0, weight=1, minsize=420, uniform="app")
        self.catalogue.columnconfigure(1, weight=1, minsize=420, uniform="app")
        self.catalogue.rowconfigure(0, weight=1)
        self.card = tk.Frame(self.catalogue, bg=BG, highlightthickness=1, highlightbackground=LINE)
        self.card.grid(row=0, column=0, sticky="new", padx=(0, 22))
        card_top = tk.Frame(self.card, bg=BG, height=48)
        card_top.pack(fill="x")
        card_top.pack_propagate(False)
        self._label(card_top, "01", "small", bg=BLUE, fg="white", padx=7, pady=5).pack(side="left", padx=17)
        self._action_button(card_top, "GITHUB ↗", "github", compact=True).pack(side="right")
        self._action_button(card_top, "WEBSITE ↗", "website", compact=True).pack(side="right")
        self._line(self.card).pack(fill="x")
        content = tk.Frame(self.card, bg=BG, padx=20, pady=21)
        content.pack(fill="x")
        identity = tk.Frame(content, bg=BG)
        identity.pack(fill="x")
        icon = self._load_image("favicon.ico", (72, 72))
        if icon:
            tk.Label(identity, image=icon, bg=BG).pack(side="left", anchor="n", padx=(0, 13))
        titlebox = tk.Frame(identity, bg=BG)
        titlebox.pack(side="left", fill="x", expand=True)
        self._label(titlebox, "Photoshop Panel", "app").pack(fill="x")
        self._label(titlebox, "Your everyday creative workflow", "body_small", fg=MUTED).pack(fill="x", pady=(3, 2))
        self._label(titlebox, "For Adobe Photoshop®", "body_small", fg=MUTED).pack(fill="x")
        self.status_badge = self._label(content, "AVAILABLE TO INSTALL", "small", fg=BLUE,
                                        padx=5, pady=5, highlightthickness=1, highlightbackground=LINE)
        self.status_badge.pack(anchor="w", pady=(22, 12))
        self.version_label = self._label(content, "", "body_small", fg=MUTED)
        self.version_label.pack(fill="x")
        self._label(content, "ORDER / BASE  ·  3D  ·  CREATIVE TOOLS", "small", fg=MUTED).pack(fill="x", pady=(18, 21))
        card_actions = tk.Frame(content, bg=BG)
        card_actions.pack(fill="x")
        self.more_button = self._button(card_actions, "···", self._show_app_menu, compact=True, width=2)
        self.more_button.pack(side="right", padx=(9, 0), fill="y")
        self.primary_button = self._button(card_actions, "INSTALL PANEL", self._primary_action, primary=True)
        self.primary_button.pack(side="left", fill="x", expand=True)
        self.secondary_button = self._action_button(content, "CHECK INSTALLATION ↗", "verify", compact=True)
        self.secondary_button.pack(anchor="w", pady=(12, 0))

        self.vector_card = tk.Frame(self.catalogue, bg=BG, highlightthickness=1, highlightbackground=LINE)
        vector_top = tk.Frame(self.vector_card, bg=BG, height=48)
        vector_top.pack(fill="x")
        vector_top.pack_propagate(False)
        self._label(vector_top, "02", "small", bg=ORANGE, fg="white", padx=7, pady=5).pack(side="left", padx=17)
        self._action_button(vector_top, "GITHUB ↗", "github", compact=True).pack(side="right")
        self._action_button(vector_top, "WEBSITE ↗", "vector_website", compact=True).pack(side="right")
        self._line(self.vector_card).pack(fill="x")
        vector_content = tk.Frame(self.vector_card, bg=BG, padx=20, pady=21)
        vector_content.pack(fill="x")
        vector_identity = tk.Frame(vector_content, bg=BG)
        vector_identity.pack(fill="x")
        vector_icon = self._load_image("vector-migic.png", (72, 72))
        if vector_icon:
            tk.Label(vector_identity, image=vector_icon, bg=BG).pack(side="left", anchor="n", padx=(0, 13))
        vector_title = tk.Frame(vector_identity, bg=BG)
        vector_title.pack(side="left", fill="x", expand=True)
        self._label(vector_title, "Vector Migic", "app").pack(fill="x")
        self._label(vector_title, "Raster to vector. On your computer.", "body_small", fg=MUTED).pack(fill="x", pady=(3, 2))
        self._label(vector_title, "PNG / JPG → SVG · Offline CPU", "body_small", fg=MUTED).pack(fill="x")
        self.vector_badge = self._label(vector_content, "AVAILABLE TO INSTALL", "small", fg=BLUE,
                                        padx=5, pady=5, highlightthickness=1, highlightbackground=LINE)
        self.vector_badge.pack(anchor="w", pady=(22, 12))
        self.vector_version = self._label(vector_content, "", "body_small", fg=MUTED)
        self.vector_version.pack(fill="x")
        self._label(vector_content, "COLOR / BLACK & WHITE · SVG", "small", fg=MUTED).pack(fill="x", pady=(18, 21))
        vector_actions = tk.Frame(vector_content, bg=BG)
        vector_actions.pack(fill="x")
        self.vector_more = self._button(vector_actions, "···", self._show_vector_menu, compact=True, width=2)
        self.vector_more.pack(side="right", padx=(9, 0), fill="y")
        self.vector_primary = self._button(vector_actions, "INSTALL VECTOR MIGIC", self._vector_action, primary=True)
        self.vector_primary.pack(side="left", fill="x", expand=True)

        self.future = tk.Frame(self.catalogue, bg=BG)
        self.future.grid(row=0, column=1, sticky="nsew")
        self.future.columnconfigure(0, weight=1)
        preview = self._load_image("photoshop-panel-preview.png", (108, 250))
        if preview:
            preview_frame = tk.Frame(self.future, bg=LINE, padx=5, pady=5)
            preview_frame.grid(row=0, column=1, rowspan=4, sticky="ne", padx=(15, 0))
            tk.Label(preview_frame, image=preview, bg=LINE, borderwidth=0).pack()
        self._label(self.future, "MADE FOR YOUR WORKFLOW", "small", fg=MUTED).grid(row=0, column=0, sticky="nw", pady=(13, 17))
        self._label(self.future, "Less switching.\nMore creating.", "title", justify="left").grid(row=1, column=0, sticky="nw")
        self._label(self.future, "Install, update and manage your\nPhotoshop tools in one place.", "body", fg=MUTED, justify="left").grid(row=2, column=0, sticky="nw", pady=18)
        self._label(self.future, "01 APP TODAY / MORE TO COME", "small", fg=BLUE).grid(row=3, column=0, sticky="nw", pady=(15, 0))
        self._line(self.future).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(31, 15))
        self._label(self.future, "A growing home for creative tools.", "body_small", fg=MUTED).grid(row=5, column=0, columnspan=2, sticky="w")

        self.table = tk.Frame(self.catalogue, bg=BG, highlightthickness=1, highlightbackground=LINE)
        headings = tk.Frame(self.table, bg="#e9e8e2", padx=17, pady=10)
        headings.pack(fill="x")
        self._label(headings, "APPLICATION", "small", bg="#e9e8e2", fg=MUTED).pack(side="left")
        self._label(headings, "STATUS / VERSION / ACTION", "small", bg="#e9e8e2", fg=MUTED).pack(side="right")
        self._line(self.table).pack(fill="x")
        row = tk.Frame(self.table, bg=BG, padx=17, pady=22)
        self.panel_row = row
        row.pack(fill="x")
        if icon:
            tk.Label(row, image=icon, bg=BG).pack(side="left", padx=(0, 15))
        self._label(row, "Photoshop Panel", "heading").pack(side="left")
        self.table_action = self._button(row, "INSTALL PANEL", self._primary_action, primary=True, compact=True)
        self.table_action.pack(side="right", padx=(18, 0))
        self._button(row, "···", self._show_app_menu, compact=True).pack(side="right", padx=(14, 0))
        self.table_status = self._label(row, "", "small", fg=BLUE)
        self.table_status.pack(side="right")

        self.vector_row = tk.Frame(self.table, bg=BG, padx=17, pady=22)
        if vector_icon:
            tk.Label(self.vector_row, image=vector_icon, bg=BG).pack(side="left", padx=(0, 15))
        self._label(self.vector_row, "Vector Migic", "heading").pack(side="left")
        self.vector_table_action = self._button(self.vector_row, "INSTALL VECTOR MIGIC", self._vector_action,
                                                primary=True, compact=True)
        self.vector_table_action.pack(side="right", padx=(18, 0))
        self.vector_table_more = self._button(self.vector_row, "···", self._show_vector_menu, compact=True)
        self.vector_table_more.pack(side="right", padx=(14, 0))
        self.vector_table_status = self._label(self.vector_row, "", "small", fg=BLUE)
        self.vector_table_status.pack(side="right")

        self.empty = tk.Frame(self.catalogue, bg=BG, highlightbackground=LINE, highlightthickness=1, padx=28, pady=38)
        self.empty_title = self._label(self.empty, "No apps in this view", "heading")
        self.empty_title.pack(anchor="w")
        self.empty_text = self._label(self.empty, "", "body", fg=MUTED)
        self.empty_text.pack(anchor="w", pady=10)
        self._button(self.empty, "SHOW ALL APPS", self._reset_filters, compact=True).pack(anchor="w", pady=(7, 0))

        self.progress = ttk.Progressbar(self.body, style="Mitom.Horizontal.TProgressbar", maximum=100, mode="determinate")
        self.progress.pack(side="bottom", fill="x", pady=(16, 9), before=self.catalogue_host)
        self.progress.configure(value=0)
        statusrow = tk.Frame(self.body, bg=BG)
        statusrow.pack(side="bottom", fill="x", before=self.progress)
        self.status_text = self._label(statusrow, "Ready", "body_small", fg=MUTED)
        self.status_text.pack(side="left", fill="x", expand=True)
        self.logs_button = self._button(statusrow, "ACTIVITY LOG ↓", self._toggle_logs, compact=True)
        self.logs_button.pack(side="right")

        self.log_frame = tk.Frame(self.root, bg=INK, padx=15, pady=10)
        log_header = tk.Frame(self.log_frame, bg=INK)
        log_header.pack(fill="x", pady=(0, 7))
        self._label(log_header, "ACTIVITY LOG", "small", fg="#e0e2e7", bg=INK).pack(side="left")
        _CaptionButton(log_header, text="CLOSE ×", command=self._toggle_logs, font=self.fonts["small"],
                  fg="#e0e2e7", bg=INK, activebackground="#303033", activeforeground="white",
                  relief="flat", borderwidth=0, cursor="hand2").pack(side="right")
        self.log_text = tk.Text(self.log_frame, height=7, bg=INK, fg="#d6d9df", insertbackground="white",
                                relief="flat", borderwidth=0, font=self.fonts["mono"], wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(fill="both", expand=True)

        self.footer = tk.Frame(self.root, bg=BG, height=50, highlightbackground=LINE, highlightthickness=1, padx=19)
        self.footer.pack(side="bottom", fill="x", before=self.body)
        self.footer.pack_propagate(False)
        self.checked_label = self._label(self.footer, "", "small", fg=MUTED)
        self.checked_label.pack(side="left", fill="y")
        self.platform_label = self._label(self.footer, "", "small", fg=MUTED)
        self.platform_label.pack(side="right", fill="y")
        self._action_button(self.footer, "WEBSITE ↗", "website", compact=True).pack(side="right", padx=15)

    def _load_image(self, filename, size):
        path = ASSETS / filename
        if not path.is_file():
            return None
        try:
            if Image is not None:
                im = Image.open(path).convert("RGBA")
                im.thumbnail(size, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(im, master=self.root)
            else:
                photo = tk.PhotoImage(file=str(path), master=self.root)
                factor = max(1, (photo.width() + size[0] - 1) // size[0], (photo.height() + size[1] - 1) // size[1])
                photo = photo.subsample(factor)
            self._images.append(photo)
            return photo
        except (OSError, tk.TclError):
            return None

    def _size_catalogue(self, _event=None):
        if not hasattr(self, "catalogue_window"):
            return
        available = max(1, self.catalogue_canvas.winfo_height())
        needed = self.catalogue.winfo_reqheight()
        overflow = needed > available + 2
        if overflow and not self.catalogue_scrollbar.winfo_manager():
            self.catalogue_scrollbar.pack(side="right", fill="y")
        elif not overflow and self.catalogue_scrollbar.winfo_manager():
            self.catalogue_scrollbar.pack_forget()
        width = max(1, self.catalogue_canvas.winfo_width())
        self.catalogue_canvas.itemconfigure(self.catalogue_window, width=width, height=max(available, needed))
        self.catalogue_canvas.configure(scrollregion=(0, 0, width, max(available, needed)))
        if not overflow:
            self.catalogue_canvas.yview_moveto(0)

    def _scroll_catalogue(self, event):
        if not self.catalogue_scrollbar.winfo_manager():
            return
        left, top = self.catalogue_canvas.winfo_rootx(), self.catalogue_canvas.winfo_rooty()
        if left <= event.x_root <= left + self.catalogue_canvas.winfo_width() and top <= event.y_root <= top + self.catalogue_canvas.winfo_height():
            self.catalogue_canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

    def _invoke(self, key):
        if self._busy and key not in ("website", "github"):
            return
        action = self.actions.get(key)
        if not callable(action):
            return
        try:
            action()
        except Exception as exc:
            self.log(f"{key}: {exc}")
            self.set_busy(False)
            self.set_state({"status": str(exc)})
            messagebox.showerror("MITOM Launcher", str(exc), parent=self.root)

    def _primary_key(self):
        if not self.state.get("installed"):
            return "install"
        if self.state.get("update_available"):
            return "install_update" if callable(self.actions.get("install_update")) else "install"
        return "open_photoshop"

    def _primary_action(self):
        self._invoke(self._primary_key())

    def _vector_key(self):
        return "open_vector" if self.state.get("vector_current_verified") else "install_vector"

    def _vector_action(self):
        self._invoke(self._vector_key())

    def _banner_action(self):
        self._invoke(self._banner_key)

    def _set_filter(self, key):
        self._filter = key
        self._render_catalogue()

    def _set_layout(self, mode):
        self._layout = mode
        self._render_catalogue()

    def _reset_filters(self):
        self._filter = "all"
        self.search.set("")
        self._render_catalogue()

    def _render_catalogue(self):
        counts, entries = catalogue_selection(self.state, self._filter, self.search.get(), self.sort.get())
        titles = {"all": "ALL", "installed": "INSTALLED", "updates": "UPDATES", "available": "NOT INSTALLED"}
        for key, button in self.filter_buttons.items():
            chosen = key == self._filter
            button.configure(text=f"{self._t(titles[key])} {counts[key]}", bg=INK if chosen else BG,
                             fg=PAPER if chosen else MUTED, highlightbackground=INK if chosen else LINE)
        for key, button in (("grid", self.grid_button), ("table", self.table_button)):
            button.configure(bg=INK if self._layout == key else BG, fg=PAPER if self._layout == key else MUTED)
        self.card.grid_remove()
        self.vector_card.grid_remove()
        self.future.grid_remove()
        self.table.grid_remove()
        self.empty.grid_remove()
        self.panel_row.pack_forget()
        self.vector_row.pack_forget()
        count = len(entries)
        self.app_count.configure(text=f"{count} {'ỨNG DỤNG' if self.state.get('language') == 'vi' else 'APP' if count == 1 else 'APPS'}")
        if not entries:
            self.empty.grid(row=0, column=0, columnspan=2, sticky="new")
            self.empty_text.configure(text=self._t("Try another search or show all apps."))
        elif self._layout == "table":
            self.table.grid(row=0, column=0, columnspan=2, sticky="new")
            for item in entries:
                (self.panel_row if item["id"] == "panel" else self.vector_row).pack(fill="x")
        else:
            for index, item in enumerate(entries):
                (self.card if item["id"] == "panel" else self.vector_card).grid(
                    row=0, column=index, sticky="new", padx=(0, 18) if index == 0 and count > 1 else 0)
        self.root.after_idle(self._size_catalogue)

    def _render(self):
        for widget, text in self._language_widgets:
            if widget.winfo_exists():
                widget.configure(text=self._t(text))
        installed = bool(self.state.get("installed"))
        update = bool(self.state.get("update_available")) and installed
        latest = self.state.get("latest_version") or ""
        version = self.state.get("installed_version") if installed else latest
        if self.state.get("preview"):
            badge, banner = "PREVIEW", "Desktop interface preview · installation actions are disabled."
            self._banner_key = "website"
            banner_action = "WEBSITE ↗"
        elif self.state.get("launcher_update_available"):
            badge = "UPDATE"
            banner = f"MITOM Launcher {self.state.get('launcher_latest_version', '')} is available."
            self._banner_key = "update_launcher" if callable(self.actions.get("update_launcher")) else "install_update"
            banner_action = "UPDATE & RESTART"
        elif update:
            badge, banner = "UPDATE", f"Photoshop Panel {latest} is available."
            self._banner_key = self._primary_key()
            banner_action = "UPDATE PANEL"
        elif installed:
            badge, banner = "READY", "Photoshop Panel is installed. Your creative tools are ready."
            self._banner_key = "check_updates"
            banner_action = "CHECK FOR UPDATES"
        else:
            badge, banner = "READY", "Your creative workflow starts with Photoshop Panel."
            self._banner_key = "install"
            banner_action = "INSTALL PANEL"
        self.banner_badge.configure(text=self._t(badge))
        self.banner_text.configure(text=self._t(banner))
        self.banner_button.configure(text=self._t(banner_action))
        state_title = "UPDATE AVAILABLE" if update else "INSTALLED · UP TO DATE" if installed else "AVAILABLE TO INSTALL"
        self.status_badge.configure(text=self._t(state_title), fg=BLUE if not installed or update else GREEN)
        self.version_label.configure(text=f"Version {version}" if version else "Version information available after checking updates")
        primary_title = "UPDATE PANEL" if update else "OPEN PHOTOSHOP" if installed else "INSTALL PANEL"
        self.primary_button.configure(text=self._t(primary_title))
        self.table_action.configure(text=self._t(primary_title))
        self.table_status.configure(text=f"{self._t(state_title)}\n{version or '—'}", fg=GREEN if installed and not update else BLUE)
        vector_installed = bool(self.state.get("vector_installed"))
        vector_available = bool(self.state.get("vector_available"))
        vector_update = bool(self.state.get("vector_update_available"))
        vector_repair = bool(self.state.get("vector_repair_required"))
        vector_status = ("UPDATE AVAILABLE" if vector_update else "REPAIR REQUIRED" if vector_repair else
                         "INSTALLED · UP TO DATE" if vector_installed else "AVAILABLE TO INSTALL" if vector_available else "NOT INCLUDED")
        vector_version = self.state.get("vector_version") or self.state.get("vector_latest_version") or "—"
        if vector_update:
            vector_version += " → " + self.state.get("vector_latest_version", "")
        vector_title = ("UPDATE VECTOR MIGIC" if vector_update else "REPAIR VECTOR MIGIC" if vector_repair else
                        "OPEN VECTOR MIGIC" if self.state.get("vector_current_verified") else "INSTALL VECTOR MIGIC")
        vector_color = GREEN if self.state.get("vector_current_verified") else BLUE
        self.vector_badge.configure(text=self._t(vector_status), fg=vector_color)
        self.vector_version.configure(text=("Phiên bản " if self.state.get("language") == "vi" else "Version ") + vector_version)
        self.vector_table_status.configure(text=f"{self._t(vector_status)}\n{vector_version}", fg=vector_color)
        for button in (self.vector_primary, self.vector_table_action):
            button.configure(text=self._t(vector_title))
            self._set_button_availability(button, self._vector_key())
            if not vector_available:
                button.configure(state="disabled")
        for button in (self.vector_more, self.vector_table_more):
            button.configure(state="disabled" if self._busy or not vector_available else "normal")
        self.status_text.configure(text=self._t(self._busy_text if self._busy else self.state.get("status", "Ready")))
        self.checked_label.configure(text=f"{self.state.get('checked_at', 'Not checked yet')} · MITOM RELEASES".upper())
        launcher_version = self.state.get("launcher_version") or "DEV"
        self.platform_label.configure(text=f"v{launcher_version} / WINDOWS {platform.machine().upper()}")
        for button, key in self._action_buttons:
            if not button.winfo_exists():
                continue
            available = callable(self.actions.get(key))
            enabled = available and (not self._busy or key in ("website", "github"))
            if self.state.get("preview") and key not in ("website", "github", "check_updates"):
                enabled = False
            button.configure(state="normal" if enabled else "disabled")
        self._set_button_availability(self.primary_button, self._primary_key())
        self._set_button_availability(self.table_action, self._primary_key())
        self._set_button_availability(self.banner_button, self._banner_key)
        self.more_button.configure(state="disabled" if self._busy else "normal")
        self._render_catalogue()

    def _show_vector_menu(self):
        menu = tk.Menu(self.root, tearoff=False, bg=PAPER, fg=INK, activebackground=BLUE,
                       activeforeground="white", font=self.fonts["body_small"])
        for title, key in (("OPEN VECTOR MIGIC", "open_vector"),
                           ("Repair / reinstall Vector Migic", "install_vector"),
                           ("Website ↗", "vector_website")):
            enabled = callable(self.actions.get(key)) and not self._busy and not self.state.get("preview")
            if key == "open_vector" and not self.state.get("vector_current_verified"):
                enabled = False
            menu.add_command(label=self._t(title), command=lambda k=key: self._invoke(k),
                             state="normal" if enabled else "disabled")
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _set_button_availability(self, button, key):
        enabled = callable(self.actions.get(key)) and (not self._busy or key in ("website", "github"))
        if self.state.get("preview") and key not in ("website", "github", "check_updates"):
            enabled = False
        button.configure(state="normal" if enabled else "disabled")

    def _show_app_menu(self):
        menu = tk.Menu(self.root, tearoff=False, bg=PAPER, fg=INK, activebackground=BLUE,
                       activeforeground="white", font=self.fonts["body_small"])
        for title, key in (("Install / repair panel", "install"), ("Check installation", "verify"),
                           ("Check for updates", "check_updates"), ("Open Photoshop", "open_photoshop"),
                           ("Chrome / Codex", "chrome"), ("Chrome Companion folder", "companion"),
                           ("Restore previous version", "rollback")):
            enabled = callable(self.actions.get(key)) and not self._busy and not self.state.get("preview")
            menu.add_command(label=title, command=lambda k=key: self._invoke(k), state="normal" if enabled else "disabled")
        menu.add_separator()
        for title, key in (("Website ↗", "website"), ("GitHub ↗", "github")):
            menu.add_command(label=title, command=lambda k=key: self._invoke(k),
                             state="normal" if callable(self.actions.get(key)) else "disabled")
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _open_settings(self):
        if self._settings is not None and self._settings.winfo_exists():
            self._settings.lift()
            return
        dialog = tk.Toplevel(self.root)
        self._settings = dialog
        dialog.title("MITOM · Settings & installation")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        panel = tk.Frame(dialog, bg=BG, padx=26, pady=24)
        panel.pack(fill="both", expand=True)
        self._label(panel, "Settings & installation", "app").pack(anchor="w", pady=(0, 20))
        self._label(panel, "LANGUAGE", "small", fg=MUTED).pack(anchor="w", pady=(0, 5))
        language = tk.StringVar(value="Tiếng Việt" if self.state.get("language") == "vi" else "English")
        language_choice = ttk.Combobox(panel, textvariable=language, values=("English", "Tiếng Việt"),
                                       state="readonly", style="Mitom.TCombobox", width=24)
        language_choice.pack(anchor="w", pady=(0, 8))
        def switch_language(_event):
            selected = "vi" if language.get() == "Tiếng Việt" else "en"
            save = self.actions.get("save_language")
            if callable(save) and not self.state.get("preview"):
                save(selected)
            self.state["language"] = selected
            self._render()
        language_choice.bind("<<ComboboxSelected>>", switch_language)
        fields = (
            ("LAUNCHER", self.state.get("launcher_version") or "Development"),
            ("PANEL", self.state.get("installed_version") or "Not installed"),
            ("VECTOR MIGIC", self.state.get("vector_version") or "Not installed"),
            ("INSTALL LOCATION", self.state.get("installed_path") or "Available after installation"),
            ("DOWNLOAD CACHE", self.state.get("cache_path") or "Managed by the launcher"),
            ("CODE SIGNING", self.state.get("signature_status") or "Status not provided by backend"),
        )
        for label, value in fields:
            self._label(panel, label, "small", fg=MUTED).pack(anchor="w", pady=(10, 4))
            self._label(panel, str(value), "body_small", wraplength=530).pack(anchor="w")
        self._label(panel, "WEBSITE", "small", fg=MUTED).pack(anchor="w", pady=(16, 5))
        website = tk.StringVar(value=self.state.get("website", ""))
        editable = callable(self.actions.get("save_settings"))
        entry = tk.Entry(panel, textvariable=website, width=65, font=self.fonts["body_small"],
                         bg=PAPER, fg=INK, relief="solid", borderwidth=1,
                         state="normal" if editable else "readonly", readonlybackground=BG)
        entry.pack(fill="x", ipady=7)
        self._label(panel, "Personal login and machine settings stay on this computer.", "body_small", fg=MUTED).pack(anchor="w", pady=(18, 15))
        buttons = tk.Frame(panel, bg=BG)
        buttons.pack(fill="x")
        if callable(self.actions.get("open_cache")):
            self._action_button(buttons, "OPEN CACHE", "open_cache", compact=True).pack(side="left", padx=(0, 9))
        if callable(self.actions.get("clear_cache")):
            self._action_button(buttons, "CLEAR CACHE", "clear_cache", compact=True).pack(side="left", padx=(0, 9))
        if editable:
            def save():
                try:
                    self.actions["save_settings"]({"website": website.get().strip()})
                    self.set_state({"website": website.get().strip()})
                    dialog.destroy()
                except Exception as exc:
                    messagebox.showerror("MITOM settings", str(exc), parent=dialog)
            self._button(buttons, "SAVE", save, primary=True, compact=True).pack(side="right", padx=(9, 0))
        self._button(buttons, "CLOSE", dialog.destroy, compact=True).pack(side="right")
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _toggle_logs(self):
        self._logs_open = not self._logs_open
        if self._logs_open:
            # A bottom sheet keeps the catalogue and window size stable. Its own
            # close control stays reachable even on a compact laptop display.
            self.log_frame.place(relx=0, rely=1, y=-50, anchor="sw", relwidth=1, height=230)
            self.log_frame.lift()
        else:
            self.log_frame.place_forget()
        self.logs_button.configure(text=f"ACTIVITY LOG {'↑' if self._logs_open else '↓'}" + (f" ({self._log_count})" if self._log_count else ""))

    def _escape(self, _event=None):
        if self._logs_open:
            self._toggle_logs()

    def _on_destroy(self, event):
        if event.widget is self.root:
            self._alive = False

    def set_state(self, state):
        self._events.put(("state", dict(state)))

    def log(self, message):
        self._events.put(("log", str(message)))

    def set_busy(self, busy, message="Working…"):
        self._events.put(("busy", (bool(busy), str(message))))

    def set_progress(self, value=None, maximum=100, message=None):
        """value=None starts an indeterminate indicator; numeric values are clamped."""
        self._events.put(("progress", (value, maximum, message)))

    def _drain_events(self):
        if not self._alive:
            return
        rerender = False
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind == "state":
                    self.state.update(value)
                    rerender = True
                elif kind == "busy":
                    self._busy, self._busy_text = value
                    if self._busy:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(15)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=self._progress)
                    rerender = True
                elif kind == "progress":
                    amount, maximum, message = value
                    self.progress.stop()
                    if amount is None:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(15)
                    else:
                        limit = max(1, float(maximum))
                        self._progress = max(0, min(100, float(amount) / limit * 100))
                        self.progress.configure(mode="determinate", maximum=100, value=self._progress)
                    if message is not None:
                        self._busy_text = str(message)
                        self.state["status"] = str(message)
                        rerender = True
                elif kind == "log":
                    self._log_count += 1
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", value.rstrip() + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                    self.logs_button.configure(text=f"ACTIVITY LOG {'↑' if self._logs_open else '↓'} ({self._log_count})")
        except queue.Empty:
            pass
        if rerender:
            self._render()
        if self._alive:
            self.root.after(40, self._drain_events)


def _demo():
    root = tk.Tk()
    # Preview stays read-only, including external links.
    ui = MITOMLauncherUI(root, {}, {"preview": True, "launcher_version": "2026.10.20",
                                    "latest_version": "2026.10.20", "status": "Preview · no files will be changed"})
    ui.log("MITOM Launcher interface preview. Installation actions are disabled.")
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MITOM Launcher desktop interface")
    parser.add_argument("--demo", action="store_true", help="Open a read-only interface preview")
    args = parser.parse_args()
    if args.demo:
        _demo()
    else:
        parser.error("Run the launcher entry point, or use --demo for the UI preview.")
