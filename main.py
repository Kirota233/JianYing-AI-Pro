"""
剪映 AI ToolBox — 全自动生产力工具箱
Built with CustomTkinter for a modern native look.
"""

import customtkinter as ctk
from tkinter import ttk, filedialog
import tkinter as tk
import threading, sys, time, os, re, json, subprocess
import pyautogui, requests
from PIL import Image
from pynput import keyboard
from google import genai
from google.genai import types

pyautogui.FAILSAFE = False

# ─── 版本与授权 ─────────────────────────────────────────────
AUTH_URL  = "https://raw.githubusercontent.com/Kirota233/JianYing-AI-Pro/master/auth.json"
VERSION   = "1.4.6"
CFG_FILE  = "config.json"
DEFAULT_KEY = "AIzaSyDQ4s-9ynGQcJw6oNDF5G2fNewnuF1zkaY"

# ─── 颜色常量 ──────────────────────────────────────────────
C_BG       = "#f0f2f5"
C_CARD     = "#ffffff"
C_PRIMARY  = "#3b82f6"
C_SUCCESS  = "#22c55e"
C_DANGER   = "#ef4444"
C_WARN     = "#f59e0b"
C_TEXT     = "#1e293b"
C_MUTED    = "#94a3b8"
C_LOG_BG   = "#f8fafc"
C_BORDER   = "#e2e8f0"

# ─── 工具函数 ──────────────────────────────────────────────
def fmt_time(us):
    ms = int(us)//1000; s, ms = divmod(ms, 1000); m, s = divmod(s, 60); h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# ─── 主应用 ────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        
        self.title(f"剪映 AI ToolBox  v{VERSION}")
        self.geometry("520x520")
        self.resizable(False, False)
        self.attributes('-topmost', True)
        
        # 屏幕右下角定位
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{sw-540}+{sh-580}")
        
        # 默认推荐坐标 (基于 2560x1440)
        self.default_res = (2560, 1440)
        self.default_coords = {"export": [2420, 19], "confirm": [1468, 1028], "popup_close": [1548, 951], "draft_close": [2538, 18]}
        self.default_color = (99, 149, 200)
        
        # 数据
        self.sys_res = pyautogui.size()
        if self.sys_res == self.default_res:
            self.coords = self.default_coords.copy()
            self.close_color = self.default_color
        else:
            self.coords = {"export": None, "confirm": None, "popup_close": None, "draft_close": None}
            self.close_color = None
            
        self.stop_flag = False
        self.rec_state = None
        self.api_key = DEFAULT_KEY
        self.api_model = "gemini-3.5-flash"
        self.drafts = []          
        self.sel_files = []
        self.rename_map = []
        self.first_run = True
        self.first_sub = True
        
        self._load_cfg()
        self._build_ui()
        
        # 启动提示 (稍加延迟确保窗口已渲染)
        self.after(500, self._show_startup_guide)
        self.after(800, self._check_auth)
        
        # 热键
        self.kb = keyboard.Listener(on_release=self._on_key)
        self.kb.start()

    # ─── 授权与自毁逻辑移到类内，方便使用内建弹窗 ───
    def _check_auth(self):
        if "placeholder" in AUTH_URL: return
        try:
            # 优先调用 GitHub REST API（无缓存，实时），失败则降级使用 Raw 域名
            api_url = "https://api.github.com/repos/Kirota233/JianYing-AI-Pro/contents/auth.json?ref=master"
            headers = {"Cache-Control": "no-cache"}
            try:
                r = requests.get(api_url, headers=headers, timeout=5).json()
                if "content" in r:
                    import base64
                    d = json.loads(base64.b64decode(r["content"]).decode("utf-8"))
                else:
                    raise Exception("Fallback")
            except:
                d = requests.get(f"{AUTH_URL}?t={time.time()}", headers=headers, timeout=5).json()
                
            if d.get("status") == "destroy":  self._self_destruct()
            if d.get("status") == "blocked":
                self._show_toast("授权失效", "该软件未获授权或已过期", C_DANGER, 5000)
                self.after(5000, sys.exit)
            rv, uu = d.get("version", VERSION), d.get("update_url", "")
            if rv != VERSION and uu:
                self._ask_yes_no("发现新版本", f"v{rv} 可用，是否立即更新？", lambda: self._update(uu))
        except Exception: pass

    def _update(self, url):
        import urllib.request
        exe = os.path.abspath(sys.argv[0])
        if not exe.endswith('.exe'): return
        self._show_toast("更新中", "正在后台下载，完成后将自动重启", C_PRIMARY, 8000)
        new = exe + ".new"
        try:
            urllib.request.urlretrieve(url, new)
            bat = os.path.join(os.environ['TEMP'], "upd.bat")
            with open(bat, "w") as f:
                f.write(f'@echo off\nping 127.0.0.1 -n 4>nul\ndel "{exe}" /f/q\nmove/y "{new}" "{exe}"\nstart "" "{exe}"\ndel "%~f0" /f/q\n')
            subprocess.Popen(bat, creationflags=0x08000000)
            os._exit(0)
        except Exception as e: 
            self._log(f"更新失败: {e}")

    def _self_destruct(self):
        exe = os.path.abspath(sys.argv[0])
        cfg = os.path.abspath(CFG_FILE)
        bat = os.path.join(os.environ['TEMP'], "rm.bat")
        with open(bat, "w") as f:
            f.write(f'@echo off\nping 127.0.0.1 -n 3>nul\ndel "{cfg}" /f/q 2>nul\n')
            if exe.endswith('.exe'): f.write(f'del "{exe}" /f/q\n')
            f.write('del "%~f0" /f/q\n')
        subprocess.Popen(bat, creationflags=0x08000000)
        os._exit(0)

    # ─── 程序内右下角 Toast 提示框 ───
    def _show_toast(self, title, message, color=C_PRIMARY, duration=4000):
        toast = ctk.CTkFrame(self, fg_color=C_CARD, border_color=color, border_width=2, corner_radius=8)
        toast.place(relx=0.96, rely=0.94, anchor="se") # 右下角内嵌
        
        hdr = ctk.CTkFrame(toast, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(hdr, text=title, font=("Segoe UI", 12, "bold"), text_color=color).pack(side="left")
        
        ctk.CTkLabel(toast, text=message, font=("Segoe UI", 11), text_color=C_TEXT, justify="left").pack(padx=12, pady=(0, 10), anchor="w")
        self.after(duration, toast.destroy)

    # ─── 程序内自定义确认框 ───
    def _ask_yes_no(self, title, message, on_yes, show_cancel=True):
        dialog = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12, border_width=2, border_color=C_BORDER)
        dialog.place(relx=0.5, rely=0.5, anchor="center")
        
        ctk.CTkLabel(dialog, text=title, font=("Segoe UI", 14, "bold"), text_color=C_TEXT).pack(pady=(20, 10))
        ctk.CTkLabel(dialog, text=message, font=("Segoe UI", 12), text_color=C_TEXT, justify="left").pack(pady=(0, 20), padx=24)
        
        bf = ctk.CTkFrame(dialog, fg_color="transparent")
        bf.pack(fill="x", padx=20, side="bottom", pady=(0, 20))
        
        def _yes(): dialog.destroy(); on_yes()
        def _no(): dialog.destroy()
        
        if show_cancel:
            ctk.CTkButton(bf, text="取消", fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER, width=100, command=_no).pack(side="left", expand=True, padx=5)
        ctk.CTkButton(bf, text="我已知晓" if not show_cancel else "确认", fg_color=C_PRIMARY, hover_color="#2563eb", width=100, command=_yes).pack(side="right", expand=True, padx=5)

    def _client(self):
        k = self.key_entry.get().strip() or DEFAULT_KEY
        return genai.Client(api_key=k)

    def _show_startup_guide(self):
        if self.first_run:
            self.first_run = False
            self._save_cfg()
            
            if self.sys_res == self.default_res:
                self.after(500, lambda: self._show_toast("✅ 坐标已自动适配", "检测到您的分辨率与默认配置匹配\n已自动为您填入推荐坐标，无需再次录制！", C_SUCCESS, 6000))
                
            msg = (
                "1. 请不要把本程序窗口遮挡住剪映的草稿封面\n"
                "2. 录制坐标请用废弃草稿进行，避免误操作\n\n"
                "【网络建议】\n"
                "本程序核心 AI 强依赖 Google Gemini。\n"
                "• 强烈建议在设置中填入您自己的 API Key\n"
                "• 使用 AI 功能时必须开启代理环境(科学上网)"
            )
            self._ask_yes_no("⚠️ 首次运行须知", msg, lambda: None, show_cancel=False)

    def _load_cfg(self):
        if os.path.exists(CFG_FILE):
            try:
                with open(CFG_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                loaded_coords = d.get("coords")
                if loaded_coords and loaded_coords.get("export"):
                    self.coords = loaded_coords
                
                loaded_color = d.get("color")
                if loaded_color:
                    self.close_color = tuple(loaded_color)
                self.api_key = d.get("api_key", DEFAULT_KEY)
                self.first_run = d.get("first_run", True)
                self.first_sub = d.get("first_sub", True)
                self.api_model = d.get("api_model", "gemini-3.5-flash")
            except: pass

    def _save_cfg(self):
        try:
            if hasattr(self, 'model_combo'): self.api_model = self.model_combo.get()
            with open(CFG_FILE, "w", encoding='utf-8') as f:
                json.dump({"coords": self.coords, "color": self.close_color,
                        "api_key": self.key_entry.get().strip(),
                        "first_run": self.first_run,
                        "first_sub": getattr(self, 'first_sub', True),
                        "api_model": getattr(self, 'api_model', "gemini-3.5-flash")}, f)
        except: pass

    # ═══════════════════════ UI 构建 ═══════════════════════
    def _build_ui(self):
        self.configure(fg_color=C_BG)
        
        # ── Tabview ──
        self.tabs = ctk.CTkTabview(self, fg_color=C_BG, segmented_button_fg_color=C_CARD,
                                    segmented_button_selected_color=C_PRIMARY,
                                    segmented_button_unselected_color=C_CARD,
                                    corner_radius=12)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        
        self.tabs.add("导出")
        self.tabs.add("字幕")
        self.tabs.add("重命名")
        self.tabs.add("设置")
        
        self._build_export_tab()
        self._build_subtitle_tab()
        self._build_rename_tab()
        self._build_settings_tab()
        
        # ── 底部状态栏 ──
        bar = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=0, height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status = ctk.CTkLabel(bar, text="就绪", font=("Segoe UI", 10), text_color=C_MUTED)
        self.status.pack(side="left", padx=12)

    # ────────── 导出页 ──────────
    def _build_export_tab(self):
        tab = self.tabs.tab("导出")
        
        # 坐标录制卡片
        card1 = self._card(tab)
        card1.pack(fill="x", padx=4, pady=(2, 4))
        
        row = ctk.CTkFrame(card1, fg_color="transparent")
        row.pack(fill="x")
        
        self.guide_lbl = ctk.CTkLabel(row, text="● 未录制坐标", font=("Segoe UI", 12),
                                       text_color=C_DANGER, anchor="w")
        self.guide_lbl.pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(row, text="引导录制 F8", width=100, height=28, corner_radius=6,
                      fg_color=C_PRIMARY, command=self._start_wizard,
                      font=("Segoe UI", 11)).pack(side="right")
        
        if self.coords.get("export"):
            self.guide_lbl.configure(text="● 坐标已就绪", text_color=C_SUCCESS)

        # 任务队列卡片
        card2 = self._card(tab)
        card2.pack(fill="both", expand=True, padx=4, pady=4)
        
        # Treeview (用 ttk 但自定义样式)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Q.Treeview", background=C_CARD, foreground=C_TEXT,
                         fieldbackground=C_CARD, borderwidth=0, font=("Segoe UI", 10),
                         rowheight=26)
        style.configure("Q.Treeview.Heading", background=C_BG, foreground=C_MUTED,
                         font=("Segoe UI", 10, "bold"), borderwidth=0, relief="flat")
        style.map("Q.Treeview", background=[("selected", "#dbeafe")],
                   foreground=[("selected", C_PRIMARY)])
        
        tf = ctk.CTkFrame(card2, fg_color="transparent")
        tf.pack(fill="both", expand=True, pady=(4, 4), padx=4)
        
        cols = ("no", "name", "st")
        self.queue = ttk.Treeview(tf, columns=cols, show="headings", height=4,
                                   selectmode="browse", style="Q.Treeview")
        self.queue.heading("no", text="#")
        self.queue.heading("name", text="草稿名称")
        self.queue.heading("st", text="状态")
        self.queue.column("no", width=32, anchor="center", minwidth=32)
        self.queue.column("name", width=240, minwidth=100)
        self.queue.column("st", width=60, anchor="center", minwidth=60)
        
        qsb = ttk.Scrollbar(tf, orient="vertical", command=self.queue.yview)
        self.queue.configure(yscrollcommand=qsb.set)
        self.queue.pack(side="left", fill="both", expand=True)
        qsb.pack(side="right", fill="y")
        
        ctk.CTkButton(card2, text="📸  AI 扫描首页草稿", height=30, corner_radius=6,
                      fg_color="#e0e7ff", text_color=C_PRIMARY, hover_color="#c7d2fe",
                      font=("Segoe UI", 11), command=self._scan_thread).pack(fill="x", padx=4, pady=(0, 4))
        
        # 控制按钮
        bf = ctk.CTkFrame(tab, fg_color="transparent")
        bf.pack(fill="x", padx=4, pady=4)
        
        self.start_btn = ctk.CTkButton(bf, text="▶  从选中项开始导出", height=36,
                                        corner_radius=8, fg_color=C_SUCCESS,
                                        hover_color="#16a34a", font=("Segoe UI", 12, "bold"),
                                        command=self._start_export)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        
        ctk.CTkButton(bf, text="⏹ 中断", width=70, height=36, corner_radius=8,
                      fg_color=C_DANGER, hover_color="#dc2626",
                      font=("Segoe UI", 12, "bold"), command=self._stop).pack(side="right")

        # 日志
        card3 = self._card(tab)
        card3.pack(fill="both", expand=True, padx=4, pady=(2, 2))
        
        self.log = ctk.CTkTextbox(card3, height=60, corner_radius=6, font=("Consolas", 10),
                                   fg_color=C_LOG_BG, text_color=C_TEXT, border_width=1,
                                   border_color=C_BORDER, state="disabled",
                                   wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)
        
        # 重定向 stdout
        sys.stdout = self._LogWriter(self.log)

    # ────────── 字幕页 ──────────
    def _build_subtitle_tab(self):
        tab = self.tabs.tab("字幕")
        
        card = self._card(tab)
        card.pack(fill="x", padx=4, pady=(4, 8))
        
        ctk.CTkLabel(card, text="剪映草稿路径", font=("Segoe UI", 12, "bold"),
                     text_color=C_TEXT, anchor="w").pack(fill="x", padx=8, pady=(8,0))
        
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(6, 8))
        self.draft_dir = ctk.CTkEntry(row, placeholder_text="选择草稿目录...",
                                       corner_radius=6, border_color=C_BORDER)
        self.draft_dir.insert(0, r"D:\JianyingPro Drafts")
        self.draft_dir.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(row, text="...", width=32, height=28, corner_radius=6,
                      fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER,
                      command=self._browse_dir).pack(side="right")

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.pack(fill="x", padx=4, pady=10)
        
        ctk.CTkButton(btn_frame, text="📥  提取字幕 & 删除轨道 → 桌面/srt", height=36,
                      corner_radius=8, fg_color=C_WARN, hover_color="#d97706",
                      font=("Segoe UI", 12, "bold"), text_color="#ffffff",
                      command=self._sub_thread).pack(fill="x", pady=(0, 6))

        btn_frame2 = ctk.CTkFrame(btn_frame, fg_color="transparent")
        btn_frame2.pack(fill="x")
        
        ctk.CTkButton(btn_frame2, text="🎨 统一字体 (Noto Sans Bold)", height=36,
                      corner_radius=8, fg_color=C_PRIMARY, hover_color="#2563eb",
                      font=("Segoe UI", 12, "bold"), text_color="#ffffff",
                      command=self._fix_font_thread).pack(side="left", fill="x", expand=True, padx=(0, 3))
                      
        ctk.CTkButton(btn_frame2, text="👁️ 解除所有字幕隐藏", height=36,
                      corner_radius=8, fg_color="#10b981", hover_color="#059669",
                      font=("Segoe UI", 12, "bold"), text_color="#ffffff",
                      command=self._unhide_tracks_thread).pack(side="right", fill="x", expand=True, padx=(3, 0))
        
        # 说明
        info_card = self._card(tab)
        info_card.pack(fill="x", padx=4)
        for t in ["提取文本为 SRT 格式，并彻底删除原草稿字幕轨道",
                   "统一字体：无差别替换所有草稿字体为 Noto Sans Bold",
                   "解除隐藏：恢复所有被隐藏(闭眼)的字幕轨道显示"]:
            ctk.CTkLabel(info_card, text=f"·  {t}", font=("Segoe UI", 11),
                         text_color=C_MUTED, anchor="w").pack(fill="x", padx=8, pady=2)
                         
        card_log = self._card(tab)
        card_log.pack(fill="both", expand=True, padx=4, pady=(6, 2))
        self.sub_log = ctk.CTkTextbox(card_log, height=60, corner_radius=6, font=("Consolas", 10),
                                   fg_color=C_LOG_BG, text_color=C_TEXT, border_width=1,
                                   border_color=C_BORDER, state="disabled", wrap="word")
        self.sub_log.pack(fill="both", expand=True, padx=4, pady=4)
        sys.stdout = self._LogWriter(self.log, self.sub_log) if hasattr(self, 'log') else sys.stdout

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.draft_dir.get())
        if d:
            self.draft_dir.delete(0, "end"); self.draft_dir.insert(0, d)

    # ────────── 重命名页 ──────────
    def _build_rename_tab(self):
        tab = self.tabs.tab("重命名")
        
        card1 = self._card(tab)
        card1.pack(fill="x", padx=4, pady=(4, 6))
        
        row = ctk.CTkFrame(card1, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=8)
        ctk.CTkButton(row, text="📁 选择文件", width=90, height=28, corner_radius=6,
                      fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER,
                      command=self._sel_vids).pack(side="left")
        self.file_lbl = ctk.CTkLabel(row, text="未选择", font=("Segoe UI", 11),
                                      text_color=C_MUTED)
        self.file_lbl.pack(side="left", padx=10)
        
        card2 = self._card(tab)
        card2.pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(card2, text="命名格式", font=("Segoe UI", 12, "bold"),
                     text_color=C_TEXT, anchor="w").pack(fill="x", padx=8, pady=(8,0))
        self.fmt_entry = ctk.CTkEntry(card2, placeholder_text="例: 短剧名_第X集",
                                       corner_radius=6, border_color=C_BORDER)
        self.fmt_entry.insert(0, "短剧名称_第X集")
        self.fmt_entry.pack(fill="x", padx=8, pady=(6, 8))
        
        self.ren_preview_btn = ctk.CTkButton(tab, text="✨  AI 预览重命名", height=34, corner_radius=8,
                      fg_color="#e0e7ff", text_color=C_PRIMARY, hover_color="#c7d2fe",
                      font=("Segoe UI", 11, "bold"), command=self._ren_preview_thread)
        self.ren_preview_btn.pack(fill="x", padx=4, pady=(6, 2))
        
        self.ren_progress = ctk.CTkProgressBar(tab, mode="indeterminate", height=4, fg_color=C_BG)
        self.ren_progress.set(0)
        
        style = ttk.Style()
        style.configure("R.Treeview", background=C_CARD, foreground=C_TEXT,
                         fieldbackground=C_CARD, borderwidth=0, font=("Segoe UI", 10),
                         rowheight=24)
        style.configure("R.Treeview.Heading", background=C_BG, foreground=C_MUTED,
                         font=("Segoe UI", 10, "bold"), borderwidth=0, relief="flat")
        
        cols = ("old", "new")
        self.ren_tree = ttk.Treeview(tab, columns=cols, show="headings", height=4,
                                      style="R.Treeview")
        self.ren_tree.heading("old", text="原文件名")
        self.ren_tree.heading("new", text="新文件名")
        self.ren_tree.column("old", width=190)
        self.ren_tree.column("new", width=190)
        self.ren_tree.pack(fill="both", expand=True, padx=4, pady=2)
        
        self.ren_btn = ctk.CTkButton(tab, text="✅  执行重命名", height=36, corner_radius=8,
                                      fg_color=C_SUCCESS, hover_color="#16a34a",
                                      font=("Segoe UI", 12, "bold"), state="disabled",
                                      command=self._exec_rename)
        self.ren_btn.pack(fill="x", padx=4, pady=6)
        
        # 注册拖放支持
        try:
            import windnd
            def _on_drop(files):
                paths = [f.decode('gbk') if isinstance(f, bytes) else f for f in files]
                valid = [p for p in paths if p.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.srt', '.ass'))]
                if valid:
                    self.sel_files = list(set(self.sel_files + valid))
                    self.file_lbl.configure(text=f"{len(self.sel_files)} 个文件")
                    self.ren_btn.configure(state="disabled")
                    for i in self.ren_tree.get_children(): self.ren_tree.delete(i)
            windnd.hook_dropfiles(self.winfo_id(), func=_on_drop)
        except Exception:
            pass

    def _sel_vids(self):
        f = filedialog.askopenfilenames(
            title="选择视频或字幕文件（可多选）",
            filetypes=[("视频/字幕", "*.mp4 *.mov *.avi *.mkv *.srt *.ass"), ("全部", "*.*")]
        )
        if f:
            self.sel_files = list(f)
            self.file_lbl.configure(text=f"{len(self.sel_files)} 个文件")
            self.ren_btn.configure(state="disabled")
            for i in self.ren_tree.get_children(): self.ren_tree.delete(i)

    # ────────── 设置页 ──────────
    def _build_settings_tab(self):
        tab = self.tabs.tab("设置")
        
        card = self._card(tab)
        card.pack(fill="x", padx=4, pady=(4, 8))
        
        ctk.CTkLabel(card, text="Gemini API Key", font=("Segoe UI", 13, "bold"),
                     text_color=C_TEXT, anchor="w").pack(fill="x", padx=8, pady=(8,0))
        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(row1, text="默认为作者公用Key，有速率限制\n强烈建议使用自己的Key以保证稳定：",
                     font=("Segoe UI", 11), text_color=C_WARN, anchor="w",
                     justify="left").pack(side="left")
        import webbrowser
        ctk.CTkButton(row1, text="获取 API Key", width=80, height=26, corner_radius=6,
                      fg_color="#3b82f6", font=("Segoe UI", 11, "bold"), text_color="white",
                      command=lambda: webbrowser.open("https://aistudio.google.com/api-keys")
                      ).pack(side="right")
        
        self.key_entry = ctk.CTkEntry(card, show="•", corner_radius=6,
                                       border_color=C_BORDER, height=32)
        self.key_entry.insert(0, self.api_key)
        self.key_entry.pack(fill="x", padx=8, pady=(0, 8))
        
        row_model = ctk.CTkFrame(card, fg_color="transparent")
        row_model.pack(fill="x", padx=8, pady=(4, 12))
        ctk.CTkLabel(row_model, text="使用的模型:", font=("Segoe UI", 12, "bold"), text_color=C_TEXT).pack(side="left")
        
        models = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash", "gemini-3.7-flash", "gemini-3.8-flash"]
        self.model_combo = ctk.CTkComboBox(row_model, values=models, width=180, corner_radius=6, border_color=C_BORDER, button_color=C_BORDER)
        self.model_combo.set(self.api_model if self.api_model in models else "gemini-3.5-flash")
        self.model_combo.pack(side="right")
        
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0,8))
        
        ctk.CTkLabel(row, text="作者 - 塔奇", font=("Segoe UI", 11), text_color=C_MUTED).pack(side="left")
        
        ctk.CTkButton(row, text="保存设置", height=32, corner_radius=6,
                      fg_color=C_PRIMARY, font=("Segoe UI", 11),
                      command=lambda: (self._save_cfg(), self._show_toast("✓", "设置已保存", C_SUCCESS))
                      ).pack(side="right")

    # ═══════════════════════ 辅助组件 ═══════════════════════
    def _card(self, parent):
        return ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=8,
                            border_width=1, border_color=C_BORDER)

    class _LogWriter:
        def __init__(self, *widgets):
            self.ws = widgets
        def write(self, s):
            for w in self.ws:
                if w:
                    w.configure(state="normal")
                    w.insert("end", s)
                    w.see("end")
                    w.configure(state="disabled")
        def flush(self): pass

    def _log(self, msg):
        print(msg)

    # ═══════════════════════ 坐标向导 ═══════════════════════
    def _start_wizard(self):
        self.rec_state = "export"
        self.guide_lbl.configure(text="[1/4] 鼠标放在【导出】上 → 按 F8", text_color=C_PRIMARY)
        self._log("向导开始，请按 F8 录制各按钮位置")

    def _on_key(self, key):
        if self.rec_state:
            if key == keyboard.Key.f8:
                x, y = pyautogui.position()
                self.coords[self.rec_state] = (x, y)
                if self.rec_state == "popup_close":
                    r, g, b = pyautogui.pixel(x, y)
                    if (r > 200 and g > 200 and b > 200) or (r < 20 and g < 20 and b < 20):
                        self._log(f"  ⚠️ 取色失败 RGB({r},{g},{b})：请放在按钮的【绿色背景】上，不要指着文字！")
                        self.after(0, lambda: self._show_toast("重新录制", "请避开白色文字，指着绿色背景按F8", C_WARN, 4000))
                        return
                    self.close_color = (r, g, b)
                    self._log(f"  ✓ 弹窗/返回 ({x},{y}) RGB({r},{g},{b})")
                else:
                    self._log(f"  ✓ {self.rec_state} ({x},{y})")
                self._next_rec()
            elif key == keyboard.Key.f9 and self.rec_state == "draft_close":
                self.coords[self.rec_state] = None
                self._log(f"  ✓ 跳过 {self.rec_state}")
                self._next_rec()

    def _next_rec(self):
        flow = {
            "export":      ("confirm",     "2/4 点导出→鼠标在【确认导出】上→F8"),
            "confirm":     ("popup_close", "3/4 鼠标在【返回首页】或【关闭】上→F8"),
            "popup_close": ("draft_close", "4/4 鼠标在【关闭草稿】上→F8 (若刚点了返回首页, 按F9跳过)"),
            "draft_close": (None,          "● 坐标已就绪"),
        }
        nxt, txt = flow[self.rec_state]
        self.rec_state = nxt
        
        color = C_SUCCESS if nxt is None else C_PRIMARY
        self.after(0, lambda: self.guide_lbl.configure(text=txt, text_color=color))
        
        if nxt is None:
            self._save_cfg()
            self._log("向导完成，坐标已保存")

    # ═══════════════════════ AI 扫描 ═══════════════════════
    def _get_api_err_msg(self, e):
        err = str(e).lower()
        if "403" in err: return "API Key 无效、无权限或未开启代理(403)"
        elif "400" in err: return "请求参数错误或被拒绝(400)"
        elif "429" in err or "quota" in err or "exhausted" in err: return "API 额度已耗尽或请求过于频繁(429)"
        elif "500" in err: return "Gemini 服务器内部错误(500)"
        elif "closed" in err: return "网络连接被强行关闭 (请检查代理是否稳定)"
        elif "timeout" in err: return "网络请求超时 (请检查代理是否可用)"
        return f"发生错误: {str(e)[:50]}"

    def _get_api_err_msg(self, e):
        err = str(e).lower()
        if "403" in err: return "API Key 无效、无权限或未开启代理(403)"
        elif "400" in err: return "请求参数错误或被拒绝(400)"
        elif "429" in err or "quota" in err or "exhausted" in err: return "API 额度已耗尽或请求过于频繁(429)"
        elif "500" in err: return "Gemini 服务器内部错误(500)"
        elif "closed" in err: return "网络连接被强行关闭 (请检查代理是否稳定)"
        elif "timeout" in err: return "网络请求超时 (请检查代理是否可用)"
        return f"发生错误: {str(e)[:50]}"

    def _scan_thread(self):
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        self._log("截屏并发送 AI 识别...")
        self.status.configure(text="AI 扫描中...")
        pyautogui.screenshot("home_screen.png")
        
        try:
            img = Image.open("home_screen.png")
            prompt = (
                "You are analyzing JianYing video editor home screen.\n"
                "Find EVERY video draft thumbnail visible.\n"
                "For each, return its bounding box [ymin,xmin,ymax,xmax] normalized 0-1000 "
                "and its visible title text.\n"
                "Return ONLY raw JSON array: [{\"box\":[y1,x1,y2,x2],\"name\":\"title\"},...]\n"
                "No markdown."
            )
            client = self._client()
            r = client.models.generate_content(
                model=self.api_model, contents=[img, prompt],
                config=types.GenerateContentConfig(temperature=0.1))
            
            t = r.text.strip()
            for pfx in ["```json", "```"]:
                if t.startswith(pfx): t = t[len(pfx):]
            if t.endswith("```"): t = t[:-3]
            data = json.loads(t.strip())
            
            sw, sh = pyautogui.size()
            raw = []
            for it in data:
                b = it.get("box", [])
                nm = it.get("name", "未知")
                if len(b) == 4:
                    y1, x1, y2, x2 = b
                    raw.append((int((x1+x2)/2/1000*sw), int((y1+y2)/2/1000*sh), nm))
            
            if not raw:
                self._log("⚠  未识别到草稿"); self.status.configure(text="未识别"); return
            
            # 排序
            raw.sort(key=lambda c: c[1])
            rows, cur = [], []
            for c in raw:
                if not cur: cur.append(c)
                else:
                    if abs(c[1] - cur[0][1]) < 100: cur.append(c)
                    else:
                        cur.sort(key=lambda i: i[0]); rows.append(cur); cur = [c]
            if cur: cur.sort(key=lambda i: i[0]); rows.append(cur)
            
            self.drafts = []
            for row in rows: self.drafts.extend(row)
            
            self.after(0, self._fill_queue)
            preview = ", ".join(d[2] for d in self.drafts[:3])
            if len(self.drafts) > 3: preview += "..."
            self._log(f"识别到 {len(self.drafts)} 个: {preview}")
            self.status.configure(text=f"{len(self.drafts)} 个草稿就绪")
        except Exception as e:
            msg = self._get_api_err_msg(e)
            self._log(f"✗ {msg}"); self.status.configure(text="扫描失败")
            self.after(0, lambda m=msg: self._show_toast("AI 扫描失败", m, C_DANGER, 6000))

    def _fill_queue(self):
        for i in self.queue.get_children(): self.queue.delete(i)
        for i, (x, y, nm) in enumerate(self.drafts):
            self.queue.insert("", "end", iid=str(i), values=(i+1, nm, "待处理"))
        if self.drafts: self.queue.selection_set("0")

    # ═══════════════════════ 导出核心 ═══════════════════════
    def _stop(self):
        self.stop_flag = True
        self._log("🛑 中断"); self.start_btn.configure(state="normal")
        self.status.configure(text="已中断")

    def _wait_done(self):
        x, y = self.coords['popup_close']; tc = self.close_color
        # 强制等 5 秒让进度条弹窗完全出现
        self._log("  · 等待进度条 (5s)")
        for _ in range(5):
            if self.stop_flag: return
            time.sleep(1)
        # 轮询像素直到关闭按钮出现
        self._log("  · 等待导出完成...")
        while not self.stop_flag:
            # Check the exact point, and points 10 pixels around it to avoid text
            colors = [pyautogui.pixel(x, y), pyautogui.pixel(x, y-10), pyautogui.pixel(x, y+10), pyautogui.pixel(x-20, y)]
            done = False
            for r, g, b in colors:
                # If any nearby pixel is Bright Cyan (Fold to Home button enabled)
                if abs(r-126)+abs(g-222)+abs(b-228) < 80:
                    done = True; break
                # Or if it matches the recorded color, AND the recorded color is NOT white/gray text
                is_white_or_dark = (r > 200 and g > 200 and b > 200) or (r < 20 and g < 20 and b < 20)
                if not is_white_or_dark and abs(r-tc[0])+abs(g-tc[1])+abs(b-tc[2]) < 20:
                    done = True; break
                    
            if done:
                self._log("  ✓ 导出完成"); break
            time.sleep(2)

    def _start_export(self):
        req_coords = [self.coords["export"], self.coords["confirm"], self.coords["popup_close"]]
        if None in req_coords:
            self._show_toast("未就绪", "请先完成坐标录制", C_WARN); return
        if not self.drafts:
            self._show_toast("未就绪", "请先 AI 扫描首页", C_WARN); return
        sel = self.queue.selection()
        if not sel:
            self._show_toast("提示", "请在队列中选中一行作为起始点", C_WARN); return
        
        idx = int(sel[0])
        nm = self.drafts[idx][2]
        n = len(self.drafts) - idx
        
        self._ask_yes_no("确认开始", f"将从【{nm}】(第{idx+1}个) 开始\n共 {n} 个待导出，确认？", lambda: self._start_export_thread(idx))

    def _start_export_thread(self, idx):
        self.stop_flag = False
        self.start_btn.configure(state="disabled")
        threading.Thread(target=self._export_loop, args=(idx,), daemon=True).start()

    def _chk(self):
        if self.stop_flag: raise Exception("用户中断")

    def _qst(self, i, st):
        self.after(0, lambda: self.queue.set(str(i), "st", st))

    def _export_loop(self, start):
        try:
            total = len(self.drafts); n = total - start
            self._log(f"\n{'─'*32}\n开始导出：共 {n} 个")
            
            for i in range(start, total):
                self._chk()
                dx, dy, nm = self.drafts[i]
                self._qst(i, "▶ 处理中")
                self.status.configure(text=f"导出 {i-start+1}/{n}")
                self._log(f"\n[{i+1}/{total}] {nm}")
                
                pyautogui.click(dx, dy)
                self._log("  · 加载草稿 (10s)")
                for _ in range(10): self._chk(); time.sleep(1)
                
                self._chk(); pyautogui.click(*self.coords['export'])
                self._log("  · 点击导出")
                for _ in range(3): self._chk(); time.sleep(1)
                
                self._chk(); pyautogui.click(*self.coords['confirm'])
                self._log("  · 确认导出")
                time.sleep(1)
                pyautogui.press('enter')
                
                if self.coords['draft_close']:
                    self._wait_done(); self._chk()
                    
                    pyautogui.click(*self.coords['popup_close'])
                    self._log("  · 关闭弹窗")
                    for _ in range(3): self._chk(); time.sleep(1)
                    
                    self._chk(); pyautogui.click(*self.coords['draft_close'])
                    self._log("  · 返回首页")
                else:
                    self._log("  · 返回首页 (后台导出)")
                    time.sleep(1)
                    pyautogui.click(*self.coords['popup_close'])
                    for _ in range(4): self._chk(); time.sleep(1)
                
                self._qst(i, "✅")
                self._log("  · 休息 3.5s")
                for _ in range(7): self._chk(); time.sleep(0.5)
            
            self._log(f"\n🎉 全部完成！共导出 {n} 个")
            self.status.configure(text=f"完成 {n} 个")
        except Exception as e:
            err = str(e)
            if "用户中断" not in err:
                self.after(0, lambda: self._show_toast("导出异常终止", f"发生错误: {err[:50]}", C_DANGER, 6000))
            self._log(f"\n⏸ {err}")
        finally:
            self.after(0, lambda: self.start_btn.configure(state="normal"))

    # ═══════════════════════ 字幕提取 ═══════════════════════
    def _check_first_sub(self, next_func):
        if self.first_sub:
            self.first_sub = False
            self._save_cfg()
            msg = "【首次使用字幕功能须知】\n\n草稿必须是从云空间下载的，并且绝对不允许在剪映里双击打开过！\n\n如果打开过，请将其删除并重新从云空间下载后再操作！"
            self._ask_yes_no("⚠️ 警告", msg, next_func, show_cancel=False)
        else:
            next_func()

    def _sub_thread(self):
        def _run():
            self._ask_yes_no("安全确认", "确定执行？此操作将彻底删除字幕轨道", lambda: threading.Thread(target=self._extract_subs, daemon=True).start())
        self._check_first_sub(_run)

    def _fix_font_thread(self):
        def _run():
            self._ask_yes_no("安全确认", "确定执行？此操作将统一字体并解除隐藏", lambda: threading.Thread(target=self._fix_fonts, daemon=True).start())
        self._check_first_sub(_run)

    def _find_noto_sans_bold(self):
        import re
        dirs_to_check = [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Fonts'),
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        ]
        
        for d in dirs_to_check:
            if not os.path.exists(d): continue
            for fn in os.listdir(d):
                if not fn.lower().endswith(('.ttf', '.otf', '.ttc')): continue
                clean_name = re.sub(r'[^a-z0-9]', '', fn.lower())
                if 'notosansbold' in clean_name:
                    return os.path.join(d, fn).replace('\\', '/')
        return None

    def _unhide_tracks_thread(self):
        def _run():
            self._ask_yes_no("安全确认", "确定执行？此操作将解除所有草稿的字幕隐藏", lambda: threading.Thread(target=self._unhide_tracks, daemon=True).start())
        self._check_first_sub(_run)

    def _unhide_tracks(self):
        base = self.draft_dir.get()
        if not os.path.exists(base): self._log("✗ 草稿根目录不存在"); return
        items = []
        for fn in os.listdir(base):
            fp = os.path.join(base, fn)
            jp = os.path.join(fp, 'draft_content.json')
            if os.path.isdir(fp) and os.path.exists(jp):
                items.append((fn, jp))
        ok = 0
        for fn, jp in items:
            try:
                with open(jp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for t in data.get('tracks', []):
                    if t.get('type') == 'text':
                        t['attribute'] = 0
                with open(jp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                bak_path = jp + ".bak"
                if os.path.exists(bak_path):
                    with open(bak_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False)
                self._log(f"  ✓ {fn} 已解除隐藏")
                ok += 1
            except Exception as e:
                self._log(f"  ✗ {fn} 失败: {e}")
        self._log(f"完成，共解除隐藏 {ok} 个草稿")
        self.after(0, lambda: self._show_toast("处理完成", f"成功解除 {ok} 个草稿的隐藏状态", C_SUCCESS, 6000))

    def _fix_fonts(self):
        font_path = self._find_noto_sans_bold()
        if not font_path:
            self._log("✗ 未在系统中找到 Noto Sans Bold 字体文件，请先安装该字体！")
            self.after(0, lambda: self._show_toast("缺少字体", "未检测到 Noto Sans Bold，无法统一字体", C_DANGER, 6000))
            return
            
        tpl_font = {
            "path": font_path,
            "id": ""
        }
        self._log(f"将强制所有草稿使用系统匹配字体:\n  {font_path}")
        base = self.draft_dir.get()
        if not os.path.exists(base): self._log("✗ 草稿根目录不存在"); return
        
        items = []
        for fn in os.listdir(base):
            fp = os.path.join(base, fn)
            jp = os.path.join(fp, 'draft_content.json')
            if os.path.isdir(fp) and os.path.exists(jp):
                items.append((fn, jp))
                
        ok = 0
        for fn, jp in items:
            try:
                with open(jp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for t in data.get('materials', {}).get('texts', []):
                    try:
                        c = json.loads(t.get('content', '{}'))
                        styles = c.get('styles', [])
                        if styles:
                            styles[0]['font'] = tpl_font
                            t['content'] = json.dumps(c, ensure_ascii=False)
                    except: pass
                with open(jp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                bak_path = jp + ".bak"
                if os.path.exists(bak_path):
                    with open(bak_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False)
                self._log(f"  ✓ {fn} 字体已统一")
                ok += 1
            except Exception as e:
                self._log(f"  ✗ {fn} 失败: {e}")
                
        self._log(f"完成，共处理 {ok} 个草稿")
        self.after(0, lambda: self._show_toast("处理完成", f"成功统一 {ok} 个草稿字体\n使用: {font_path}", C_SUCCESS, 6000))

    def _extract_subs(self):
        self._log("提取字幕...")
        base = self.draft_dir.get()
        if not os.path.exists(base): self._log("✗ 目录不存在"); return
        out = os.path.join(os.path.expanduser("~"), "Desktop", "srt")
        os.makedirs(out, exist_ok=True)
        
        items = []
        for fn in os.listdir(base):
            fp = os.path.join(base, fn)
            if not os.path.isdir(fp) or fn == 'srt': continue
            jp = os.path.join(fp, 'draft_content.json')
            if not os.path.exists(jp): continue
            items.append({"jp": jp, "fn": fn})
        
        ok = 0
        for d in items:
            try:
                with open(d["jp"], 'r', encoding='utf-8') as f:
                    data = json.load(f)
                td = {t['id']: t for t in data.get('materials', {}).get('texts', [])}
                tracks = data.get('tracks', [])
                text_tracks = [t for t in tracks if t.get('type') == 'text']
                
                if not text_tracks: continue
                
                segs = []
                for tr in text_tracks: segs.extend(tr.get('segments', []))
                segs.sort(key=lambda x: x.get('target_timerange', {}).get('start', 0))
                
                lines, idx = [], 1
                for seg in segs:
                    tm = td.get(seg.get('material_id'))
                    if not tm: continue
                    try: txt = json.loads(tm.get('content', '{}')).get('text', '')
                    except: txt = tm.get('content', '')
                    if not txt.strip(): continue
                    tr = seg.get('target_timerange', {}); s = tr.get('start', 0)
                    lines += [str(idx), f"{fmt_time(s)} --> {fmt_time(s+tr.get('duration',0))}", txt.strip(), ""]
                    idx += 1
                
                if lines:
                    # 命名为原草稿文件夹名
                    sp = os.path.join(out, f"{d['fn']}.srt")
                    with open(sp, 'w', encoding='utf-8') as f:
                        f.write("\n".join(lines))
                    
                    # 彻底删除字幕轨道
                    data['tracks'] = [t for t in tracks if t.get('type') != 'text']
                    
                    # 同时清理 texts 素材库，防止残留
                    if 'materials' in data and 'texts' in data['materials']:
                        data['materials']['texts'] = []
                        
                    # 保存主配置
                    with open(d["jp"], 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False)
                    
                    # 同步覆盖 .bak 文件，防止剪映自动恢复
                    bak_path = d["jp"] + ".bak"
                    if os.path.exists(bak_path):
                        with open(bak_path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False)
                    
                    self._log(f"  ✓ {d['fn']} → {os.path.basename(sp)}")
                    ok += 1
            except Exception as e: self._log(f"  ✗ {d['fn']}: {e}")
        self._log(f"完成，提取并删除字幕 {ok} 个")
        self.after(0, lambda: self._show_toast("提取完成", f"共处理 {ok} 个草稿\n字幕已保存至桌面/srt", C_SUCCESS, 6000))

    # ═══════════════════════ 重命名 ═══════════════════════
    def _ren_preview_thread(self):
        if not self.sel_files: self._show_toast("提示", "请先选择视频/字幕文件", C_WARN); return
        self.ren_preview_btn.configure(state="disabled", text="⏳ AI 正在思考中...")
        self.ren_progress.pack(fill="x", padx=12, pady=(0, 4))
        self.ren_progress.start()
        threading.Thread(target=self._ren_preview, daemon=True).start()

    def _ren_preview(self):
        self._log("AI 分析重命名...")
        fns = [os.path.basename(f) for f in self.sel_files]
        prompt = (f"Rename these files to: {self.fmt_entry.get()}\n"
                  f"Files: {json.dumps(fns, ensure_ascii=False)}\n"
                  f"Return ONLY raw JSON array with 'old' and 'new' keys.")
        try:
            client = self._client()
            r = client.models.generate_content(
                model=self.api_model, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1))
            t = r.text.strip()
            for pfx in ["```json", "```"]:
                if t.startswith(pfx): t = t[len(pfx):]
            if t.endswith("```"): t = t[:-3]
            data = json.loads(t.strip())
            self.after(0, self._fill_ren, data)
        except Exception as e: 
            msg = self._get_api_err_msg(e)
            self._log(f"✗ {msg}")
            self.after(0, lambda m=msg: self._show_toast("AI 重命名失败", m, C_DANGER, 6000))
            self.after(0, self._reset_ren_ui)

    def _reset_ren_ui(self):
        self.ren_preview_btn.configure(state="normal", text="✨  AI 预览重命名")
        self.ren_progress.stop()
        self.ren_progress.pack_forget()

    def _fill_ren(self, data):
        self._reset_ren_ui()
        for i in self.ren_tree.get_children(): self.ren_tree.delete(i)
        dm = {os.path.basename(f): os.path.dirname(f) for f in self.sel_files}
        self.rename_map = []
        for it in data:
            o, n = it.get("old"), it.get("new")
            if o and n and o in dm:
                d = dm[o]; self.rename_map.append((os.path.join(d, o), os.path.join(d, n)))
                self.ren_tree.insert("", "end", values=(o, n))
        self.ren_btn.configure(state="normal")
        self._log("✓ 预览已生成")

    def _exec_rename(self):
        if not self.rename_map: return
        ok = 0
        for o, n in self.rename_map:
            try: os.rename(o, n); ok += 1
            except: pass
        self._show_toast("重命名完成", f"成功修改 {ok}/{len(self.rename_map)} 个文件", C_SUCCESS)
        self.sel_files = []; self.file_lbl.configure(text="未选择")
        for i in self.ren_tree.get_children(): self.ren_tree.delete(i)
        self.ren_btn.configure(state="disabled")

# ─── 启动 ──────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
