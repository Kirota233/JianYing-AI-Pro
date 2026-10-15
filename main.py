"""
剪映 AI ToolBox — 全自动生产力工具箱
Built with CustomTkinter for a modern native look.
"""

import customtkinter as ctk
from tkinter import ttk, filedialog
import tkinter as tk
import ctypes
import threading, sys, time, os, re, json, subprocess, shutil
from pathlib import Path
import pyautogui, requests
from PIL import Image
from pynput import keyboard
from google import genai
from google.genai import types

pyautogui.FAILSAFE = False

# ─── 版本与授权 ─────────────────────────────────────────────
AUTH_URL  = "https://raw.githubusercontent.com/Kirota233/JianYing-AI-Pro/master/auth.json"
VERSION   = "1.6.0"
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


def _rename_get_episode(name):
    """从文件名中提取集数，优先识别“第 N 集”格式。"""
    cleaned = re.sub(r"\(\d+\)\s*$", "", name).strip()
    match = re.search(r"第\s*(\d+)\s*集", cleaned)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*集", cleaned)
    if match:
        return int(match.group(1))
    matches = re.findall(r"\d+", cleaned)
    return int(matches[-1]) if matches else None


def _rename_is_duplicate_copy(name):
    return re.search(r"\(\d+\)\s*$", name) is not None


def _rename_parse_template(template):
    """将包含数字的示例模板拆成数字前缀和后缀。"""
    match = re.search(r"\d+", template)
    if not match:
        return None
    return template[:match.start()], template[match.end():]


def _rename_plan(folder, prefix, suffix, extensions=(".mp4", ".srt", ".mp3", ".wav", ".mkv")):
    """生成重命名计划，不修改文件。"""
    ext_set = tuple(ext.lower() for ext in extensions)
    files = [
        filename for filename in sorted(os.listdir(folder))
        if os.path.isfile(os.path.join(folder, filename))
        and filename.lower().endswith(ext_set)
    ]
    files.sort(key=lambda filename: _rename_is_duplicate_copy(os.path.splitext(filename)[0]))

    planned = []
    skipped_duplicates = []
    skipped_without_number = []
    used_targets = set()
    for filename in files:
        stem, extension = os.path.splitext(filename)
        number = _rename_get_episode(stem)
        if number is None:
            skipped_without_number.append(filename)
            continue
        new_name = f"{prefix}{number}{suffix}{extension}"
        if new_name in used_targets:
            skipped_duplicates.append((filename, new_name))
            continue
        used_targets.add(new_name)
        planned.append((filename, new_name))
    return planned, skipped_duplicates, skipped_without_number

# ─── 工具函数 ──────────────────────────────────────────────
def fmt_time(us):
    ms = int(us)//1000; s, ms = divmod(ms, 1000); m, s = divmod(s, 60); h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class _MsvcString(ctypes.Structure):
    _fields_ = [
        ("storage", ctypes.c_ubyte * 16),
        ("size", ctypes.c_size_t),
        ("capacity", ctypes.c_size_t),
    ]


def _msvc_input_string(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    backing = ctypes.create_string_buffer(raw + b"\0")
    value = _MsvcString()
    if len(raw) < 16:
        value.storage[:len(raw)] = raw
    else:
        ctypes.c_void_p.from_buffer(value.storage).value = ctypes.addressof(backing)
    value.size = len(raw)
    value.capacity = max(15, len(raw))
    return value, backing


def _msvc_output_string(value):
    if value.size > 256 * 1024 * 1024:
        raise ValueError(f"剪映接口返回了异常的数据长度: {value.size}")
    if value.capacity < 16:
        return bytes(value.storage[:value.size])
    pointer = ctypes.c_void_p.from_buffer(value.storage).value
    if not pointer:
        raise ValueError("剪映接口返回了空数据指针")
    return ctypes.string_at(pointer, value.size)


class JianyingDraftIO:
    """使用当前安装的剪映接口读写新版加密 draft_content.json。"""

    FACTORY_CTOR = b"??0DraftIOFactoryBase@draft_store@@QEAA@XZ"
    CREATE_IO = b"?createIO@DraftIOFactoryBase@draft_store@@UEAA?AV?$shared_ptr@UIDraftIO@draft_store@@@std@@PEBV?$array@D$0CA@@4@AEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@4@@Z"
    CALC_ENTROPY = b"?calcEntropyDynamicString@DraftIOFactoryBase@draft_store@@UEAA?AU?$pair@V?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@V?$array@D$0CA@@2@@std@@AEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@4@@Z"

    def __init__(self, dll_path=None):
        if os.name != "nt":
            raise RuntimeError("加密草稿读写仅支持 Windows")
        self.dll_path = Path(dll_path or self._find_videoeditor_dll()).resolve()
        self._dll_dir = os.add_dll_directory(str(self.dll_path.parent))
        self.library = ctypes.WinDLL(str(self.dll_path))
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._kernel32.GetProcAddress.restype = ctypes.c_void_p

        self.factory = (ctypes.c_ubyte * 24)()
        constructor = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p
        )(self._address(self.FACTORY_CTOR))
        constructor(ctypes.addressof(self.factory))

        self._create_io = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p
        )(self._address(self.CREATE_IO))
        self._calc_entropy = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(self._address(self.CALC_ENTROPY))

    @staticmethod
    def _find_videoeditor_dll():
        candidates = []
        override = os.environ.get("JIANYING_VIDEOEDITOR_DLL", "").strip()
        if override:
            candidates.append(Path(override))

        roots = [
            Path.home() / "JianyingPro",
            Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro",
            Path(os.environ.get("ProgramFiles", "")) / "JianyingPro",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "JianyingPro",
            Path("D:/JianyingPro"),
            Path("E:/JianyingPro"),
        ]
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Bytedance\JianyingPro")
            install_dir, _ = winreg.QueryValueEx(key, "installDir")
            if install_dir:
                roots.insert(0, Path(install_dir))
        except Exception:
            pass
        for root in roots:
            if not root.is_dir():
                continue
            direct = root / "videoeditor.dll"
            if direct.is_file():
                candidates.append(direct)
            try:
                candidates.extend(root.glob("*/videoeditor.dll"))
                candidates.extend(root.glob("*/*/videoeditor.dll"))
            except OSError:
                pass

        candidates = list({str(path.resolve()): path.resolve() for path in candidates if path.is_file()}.values())
        if not candidates:
            raise FileNotFoundError(
                "未找到剪映 videoeditor.dll；可设置环境变量 JIANYING_VIDEOEDITOR_DLL 指向该文件"
            )

        def version_key(path):
            numbers = tuple(int(part) for part in re.findall(r"\d+", path.parent.name))
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = 0
            return numbers, modified

        return max(candidates, key=version_key)

    def _address(self, name):
        result = self._kernel32.GetProcAddress(self.library._handle, name)
        if not result:
            raise RuntimeError(f"当前剪映版本缺少草稿接口: {name!r}")
        return result

    @staticmethod
    def _attachment_path(draft_path):
        draft_path = Path(draft_path).resolve()
        parents = [draft_path.parent]
        parents.extend(list(draft_path.parents)[1:5])
        for parent in parents:
            candidate = parent / "attachment_pc_common.json"
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(f"找不到 attachment_pc_common.json: {draft_path}")

    def _entropy(self, draft_path):
        attachment = self._attachment_path(draft_path)
        source_text = attachment.read_text(encoding="utf-8-sig")
        source, backing = _msvc_input_string(source_text)
        result = (ctypes.c_ubyte * 64)()
        self._calc_entropy(
            ctypes.addressof(self.factory), ctypes.addressof(result), ctypes.addressof(source)
        )
        entropy = bytes(result[32:64])
        if not any(entropy):
            raise RuntimeError(f"无法计算草稿密钥: {draft_path}")
        return entropy

    def _open(self, draft_path):
        draft_path = str(Path(draft_path).resolve())
        entropy = (ctypes.c_ubyte * 32).from_buffer_copy(self._entropy(draft_path))
        path_value, path_backing = _msvc_input_string(draft_path)
        shared_io = (ctypes.c_void_p * 2)()
        self._create_io(
            ctypes.addressof(self.factory), ctypes.addressof(shared_io),
            ctypes.addressof(entropy), ctypes.addressof(path_value)
        )
        object_pointer = shared_io[0]
        if not object_pointer:
            raise RuntimeError(f"无法打开剪映草稿: {draft_path}")
        return object_pointer

    @staticmethod
    def _virtual(object_pointer, index, argtypes):
        vtable = ctypes.c_void_p.from_address(object_pointer).value
        address = ctypes.c_void_p.from_address(vtable + index * 8).value
        return ctypes.CFUNCTYPE(ctypes.c_void_p, *argtypes)(address)

    def read_json(self, draft_path):
        object_pointer = self._open(draft_path)
        result = (ctypes.c_ubyte * 0x58)()
        read = self._virtual(
            object_pointer, 3, (ctypes.c_void_p, ctypes.c_void_p)
        )
        read(object_pointer, ctypes.addressof(result))
        result_code = ctypes.c_uint32.from_buffer(result, 0x48).value
        detail = _msvc_output_string(_MsvcString.from_buffer(result, 0x28)).decode(
            "utf-8", errors="replace"
        )
        raw = _msvc_output_string(_MsvcString.from_buffer(result, 0x08))
        if result_code:
            raise RuntimeError(f"剪映草稿解密失败({result_code}): {detail}")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"剪映草稿解密后不是有效 JSON: {draft_path}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"剪映草稿根节点不是对象: {draft_path}")
        return data

    def write_json(self, draft_path, data):
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        object_pointer = self._open(draft_path)
        value, backing = _msvc_input_string(raw)
        result = (ctypes.c_ubyte * 0x30)()
        write = self._virtual(
            object_pointer, 7,
            (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        )
        write(object_pointer, ctypes.addressof(result), ctypes.addressof(value))
        result_code = ctypes.c_uint32.from_buffer(result, 0x20).value
        detail = _msvc_output_string(_MsvcString.from_buffer(result)).decode(
            "utf-8", errors="replace"
        )
        if result_code:
            raise RuntimeError(f"剪映草稿加密写入失败({result_code}): {detail}")

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
        self.default_coords = {"export": [2420, 19], "confirm": [1468, 1028], "popup_close": [1548, 951], "close_btn": None, "draft_close": [2538, 18]}
        self.default_color = (99, 149, 200)
        
        # 数据
        self.sys_res = pyautogui.size()
        if self.sys_res == self.default_res:
            self.coords = self.default_coords.copy()
            self.close_color = self.default_color
        else:
            self.coords = {"export": None, "confirm": None, "popup_close": None, "close_btn": None, "draft_close": None}
            self.close_color = None
            
        self.stop_flag = False
        self.rec_state = None
        self.api_key = DEFAULT_KEY
        self.api_model = "gemini-3.5-flash"
        self.drafts = []          
        self.sel_files = []
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
        if getattr(sys, 'frozen', False):
            exe = os.path.abspath(sys.executable)
        else:
            exe = os.path.abspath(sys.argv[0])
        if not exe.lower().endswith('.exe'): return
        self._show_toast("更新中", "正在后台下载，完成后将自动重启", C_PRIMARY, 8000)
        new = exe + ".new"
        try:
            urllib.request.urlretrieve(url, new)
            bat = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), "upd.bat")
            with open(bat, "w", encoding="gbk") as f:
                f.write(f'''@echo off
set "EXE={exe}"
set "NEW={new}"
set /a count=0
:loop
del /f /q "%EXE%" >nul 2>&1
if not exist "%EXE%" goto replace
set /a count+=1
if %count% geq 60 goto replace
ping 127.0.0.1 -n 2 >nul
goto loop
:replace
move /y "%NEW%" "%EXE%" >nul 2>&1
start "" "%EXE%"
del /f /q "%~f0" >nul 2>&1
''')
            subprocess.Popen(bat, creationflags=0x08000000)
            os._exit(0)
        except Exception as e: 
            self._log(f"更新失败: {e}")

    def _self_destruct(self):
        if getattr(sys, 'frozen', False):
            exe = os.path.abspath(sys.executable)
        else:
            exe = os.path.abspath(sys.argv[0])
        cfg = os.path.abspath(CFG_FILE)
        bat = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), "rm.bat")
        try:
            with open(bat, "w", encoding="gbk") as f:
                f.write(f'''@echo off
set "EXE={exe}"
set "CFG={cfg}"
set /a count=0
:loop
del /f /q "%CFG%" >nul 2>&1
del /f /q "%EXE%" >nul 2>&1
if not exist "%EXE%" goto done
set /a count+=1
if %count% geq 60 goto done
ping 127.0.0.1 -n 2 >nul
goto loop
:done
del /f /q "%~f0" >nul 2>&1
''')
            subprocess.Popen(bat, creationflags=0x08000000)
            ps_cmd = f'powershell -WindowStyle Hidden -Command "$p=\\"{exe}\\"; for($i=0;$i -lt 30;$i++){{ if(Test-Path $p){{ try{{ [IO.File]::Delete($p); break }}catch{{ Start-Sleep -Milliseconds 500 }} }}else{{ break }} }}"'
            subprocess.Popen(ps_cmd, shell=True)
        except Exception: pass
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
                    if "close_btn" not in loaded_coords:
                        loaded_coords["close_btn"] = None
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
        
        ctk.CTkButton(btn_frame, text="📥  提取字幕 & 隐藏轨道 → 桌面/srt", height=36,
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
        for t in ["提取文本为 SRT 格式，并隐藏原草稿字幕轨道",
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
        self.rename_folder = ""
        self.rename_plan = []

        # 模式切换条 (明显标出：正则匹配重命名 vs AI 智能重命名)
        card_mode = self._card(tab)
        card_mode.pack(fill="x", padx=4, pady=(4, 4))
        mode_row = ctk.CTkFrame(card_mode, fg_color="transparent")
        mode_row.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(mode_row, text="模式选择:", font=("Segoe UI", 12, "bold"), text_color=C_TEXT).pack(side="left", padx=(0, 8))
        self.ren_mode_seg = ctk.CTkSegmentedButton(
            mode_row,
            values=["正则匹配重命名", "AI 智能重命名"],
            command=self._on_ren_mode_change,
            font=("Segoe UI", 11, "bold"),
            height=28
        )
        self.ren_mode_seg.set("正则匹配重命名")
        self.ren_mode_seg.pack(side="left", fill="x", expand=True)

        # 容器框架，用于动态切换两种模式的输入面板
        self.ren_body_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.ren_body_frame.pack(fill="x", padx=0, pady=0)

        # === 1. 正则匹配重命名 面板 ===
        self.regex_container = ctk.CTkFrame(self.ren_body_frame, fg_color="transparent")
        
        card_reg1 = self._card(self.regex_container)
        card_reg1.pack(fill="x", padx=4, pady=(2, 4))
        ctk.CTkLabel(card_reg1, text="目标文件夹 [正则匹配]", font=("Segoe UI", 12, "bold"),
                     text_color=C_TEXT, anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        folder_row = ctk.CTkFrame(card_reg1, fg_color="transparent")
        folder_row.pack(fill="x", padx=8, pady=(4, 6))
        self.rename_folder_entry = ctk.CTkEntry(
            folder_row, placeholder_text="选择包含视频或字幕的文件夹",
            corner_radius=6, border_color=C_BORDER
        )
        self.rename_folder_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            folder_row, text="📁", width=36, height=28, corner_radius=6,
            fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER,
            command=self._browse_rename_folder
        ).pack(side="left", padx=(6, 0))

        card_reg2 = self._card(self.regex_container)
        card_reg2.pack(fill="x", padx=4, pady=(2, 4))
        ctk.CTkLabel(card_reg2, text="命名模板 [正则匹配]", font=("Segoe UI", 12, "bold"),
                     text_color=C_TEXT, anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        self.fmt_entry = ctk.CTkEntry(
            card_reg2, placeholder_text="例：短剧名称_第1集",
            corner_radius=6, border_color=C_BORDER
        )
        self.fmt_entry.insert(0, "短剧名称_第1集")
        self.fmt_entry.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkLabel(
            card_reg2, text="提示：模板中的数字会被自动替换为文件名提取出的集数",
            font=("Segoe UI", 10), text_color=C_MUTED, anchor="w"
        ).pack(fill="x", padx=8, pady=(0, 6))

        self.regex_preview_btn = ctk.CTkButton(
            self.regex_container, text="⚡  规则预览重命名 [正则匹配]", height=32, corner_radius=8,
            fg_color="#e0e7ff", text_color=C_PRIMARY, hover_color="#c7d2fe",
            font=("Segoe UI", 11, "bold"), command=self._regex_preview_thread
        )
        self.regex_preview_btn.pack(fill="x", padx=4, pady=(2, 2))

        # === 2. AI 智能重命名 面板 ===
        self.ai_container = ctk.CTkFrame(self.ren_body_frame, fg_color="transparent")

        card_ai1 = self._card(self.ai_container)
        card_ai1.pack(fill="x", padx=4, pady=(2, 4))
        ctk.CTkLabel(card_ai1, text="选择文件 / 文件夹 [AI 智能]", font=("Segoe UI", 12, "bold"),
                     text_color=C_TEXT, anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        ai_row = ctk.CTkFrame(card_ai1, fg_color="transparent")
        ai_row.pack(fill="x", padx=8, pady=(4, 6))
        ctk.CTkButton(ai_row, text="📁 选择文件", width=85, height=28, corner_radius=6,
                      fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER,
                      command=self._sel_vids).pack(side="left")
        ctk.CTkButton(ai_row, text="📂 载入文件夹", width=85, height=28, corner_radius=6,
                      fg_color=C_BG, text_color=C_TEXT, hover_color=C_BORDER,
                      command=self._load_folder_for_ai).pack(side="left", padx=(6, 0))
        self.file_lbl = ctk.CTkLabel(ai_row, text="未选择", font=("Segoe UI", 11), text_color=C_MUTED)
        self.file_lbl.pack(side="left", padx=8)

        card_ai2 = self._card(self.ai_container)
        card_ai2.pack(fill="x", padx=4, pady=(2, 4))
        ctk.CTkLabel(card_ai2, text="命名格式 [AI 智能]", font=("Segoe UI", 12, "bold"),
                     text_color=C_TEXT, anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        self.ai_fmt_entry = ctk.CTkEntry(
            card_ai2, placeholder_text="例: 短剧名_第X集",
            corner_radius=6, border_color=C_BORDER
        )
        self.ai_fmt_entry.insert(0, "短剧名称_第X集")
        self.ai_fmt_entry.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkLabel(
            card_ai2, text="提示：由 Google Gemini 大模型智能识别文件名并按格式推理重命名",
            font=("Segoe UI", 10), text_color=C_MUTED, anchor="w"
        ).pack(fill="x", padx=8, pady=(0, 6))

        self.ai_preview_btn = ctk.CTkButton(
            self.ai_container, text="✨  AI 预览重命名 [AI 智能]", height=32, corner_radius=8,
            fg_color="#e0e7ff", text_color=C_PRIMARY, hover_color="#c7d2fe",
            font=("Segoe UI", 11, "bold"), command=self._ai_preview_thread
        )
        self.ai_preview_btn.pack(fill="x", padx=4, pady=(2, 2))

        self.ren_progress = ctk.CTkProgressBar(self.ai_container, mode="indeterminate", height=4, fg_color=C_BG)
        self.ren_progress.set(0)

        # 默认展示正则匹配面板
        self.regex_container.pack(fill="x", expand=True)

        # === 3. 共享预览表格 (清晰标出重命名方式) ===
        style = ttk.Style()
        style.configure("R.Treeview", background=C_CARD, foreground=C_TEXT,
                         fieldbackground=C_CARD, borderwidth=0, font=("Segoe UI", 10),
                         rowheight=24)
        style.configure("R.Treeview.Heading", background=C_BG, foreground=C_MUTED,
                         font=("Segoe UI", 10, "bold"), borderwidth=0, relief="flat")
        
        cols = ("old", "new", "mode")
        self.ren_tree = ttk.Treeview(tab, columns=cols, show="headings", height=5,
                                      style="R.Treeview")
        self.ren_tree.heading("old", text="原文件名")
        self.ren_tree.heading("new", text="新文件名")
        self.ren_tree.heading("mode", text="重命名方式")
        self.ren_tree.column("old", width=175)
        self.ren_tree.column("new", width=175)
        self.ren_tree.column("mode", width=80, anchor="center")
        self.ren_tree.pack(fill="both", expand=True, padx=4, pady=2)
        
        self.ren_btn = ctk.CTkButton(tab, text="✅  执行重命名", height=34, corner_radius=8,
                                      fg_color=C_SUCCESS, hover_color="#16a34a",
                                      font=("Segoe UI", 12, "bold"), state="disabled",
                                      command=self._exec_rename)
        self.ren_btn.pack(fill="x", padx=4, pady=(3, 6))

        # 注册拖放支持
        try:
            import windnd
            def _on_drop(files):
                paths = [f.decode('gbk') if isinstance(f, bytes) else f for f in files]
                if len(paths) == 1 and os.path.isdir(paths[0]):
                    folder = paths[0]
                    self.rename_folder_entry.delete(0, "end")
                    self.rename_folder_entry.insert(0, folder)
                    if self.ren_mode_seg.get() == "AI 智能重命名":
                        self._load_folder_for_ai(folder)
                    return
                valid = [p for p in paths if p.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.srt', '.ass', '.wav', '.mp3'))]
                if valid:
                    self.sel_files = list(set(self.sel_files + valid))
                    self.file_lbl.configure(text=f"{len(self.sel_files)} 个文件")
                    self.ren_mode_seg.set("AI 智能重命名")
                    self._on_ren_mode_change("AI 智能重命名")
                    self.ren_btn.configure(state="disabled")
                    for i in self.ren_tree.get_children(): self.ren_tree.delete(i)
            windnd.hook_dropfiles(self.winfo_id(), func=_on_drop)
        except Exception:
            pass

    def _on_ren_mode_change(self, mode):
        """切换重命名模式视图"""
        for item in self.ren_tree.get_children():
            self.ren_tree.delete(item)
        self.rename_plan = []
        self.ren_btn.configure(state="disabled")
        if mode == "正则匹配重命名":
            self.ai_container.pack_forget()
            self.regex_container.pack(fill="x", expand=True)
        else:
            self.regex_container.pack_forget()
            self.ai_container.pack(fill="x", expand=True)

    def _browse_rename_folder(self):
        folder = filedialog.askdirectory(title="选择要批量重命名的文件夹")
        if folder:
            self.rename_folder_entry.delete(0, "end")
            self.rename_folder_entry.insert(0, folder)
            self.rename_plan = []
            for item in self.ren_tree.get_children():
                self.ren_tree.delete(item)
            self.ren_btn.configure(state="disabled")

    def _sel_vids(self):
        f = filedialog.askopenfilenames(
            title="选择视频或字幕文件（可多选）",
            filetypes=[("视频/字幕", "*.mp4 *.mov *.avi *.mkv *.srt *.ass *.wav *.mp3"), ("全部", "*.*")]
        )
        if f:
            self.sel_files = list(f)
            self.file_lbl.configure(text=f"{len(self.sel_files)} 个文件")
            self.ren_btn.configure(state="disabled")
            for i in self.ren_tree.get_children(): self.ren_tree.delete(i)

    def _load_folder_for_ai(self, folder=None):
        if not folder:
            folder = self.rename_folder_entry.get().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            folder = filedialog.askdirectory(title="选择包含视频或字幕的文件夹")
        if folder and os.path.isdir(folder):
            self.rename_folder_entry.delete(0, "end")
            self.rename_folder_entry.insert(0, folder)
            valid_exts = ('.mp4', '.mov', '.avi', '.mkv', '.srt', '.ass', '.wav', '.mp3')
            files = [
                os.path.join(folder, f) for f in sorted(os.listdir(folder))
                if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith(valid_exts)
            ]
            if files:
                self.sel_files = files
                self.file_lbl.configure(text=f"{len(self.sel_files)} 个文件")
                for item in self.ren_tree.get_children():
                    self.ren_tree.delete(item)
                self.ren_btn.configure(state="disabled")
                self._log(f"已从文件夹载入 {len(files)} 个文件用于 AI 重命名")
            else:
                self._show_toast("未找到有效文件", "该文件夹下没有常见的视频或字幕文件", C_WARN)

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
        
        ctk.CTkLabel(row, text="作者 - 塔奇 修改 - 小马", font=("Segoe UI", 11), text_color=C_MUTED).pack(side="left")
        
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
        self.guide_lbl.configure(text="[1/5] 鼠标放在【导出】上 → 按 F8", text_color=C_PRIMARY)
        self._log("向导开始，请按 F8 录制各按钮位置")
    
    def _capture_btn_template(self, x, y, name):
        """录制时截取按钮区域保存为模板图片"""
        try:
            w, h = 100, 44
            rx, ry = max(0, x - w // 2), max(0, y - h // 2)
            img = pyautogui.screenshot(region=(rx, ry, w, h))
            base = os.path.dirname(os.path.abspath(CFG_FILE)) or "."
            path = os.path.join(base, f"tpl_{name}.png")
            img.save(path)
            self._log(f"  📸 已保存模板 → tpl_{name}.png")
        except Exception as e:
            self._log(f"  ⚠ 截图失败: {e}")

    def _on_key(self, key):
        if self.rec_state:
            if key == keyboard.Key.f8:
                x, y = pyautogui.position()
                self.coords[self.rec_state] = (x, y)
                if self.rec_state == "popup_close":
                    r, g, b = pyautogui.pixel(x, y)
                    self.close_color = (r, g, b)
                    self._capture_btn_template(x, y, "return_home")
                    self._log(f"  ✓ 收起至首页 ({x},{y}) RGB({r},{g},{b})")
                elif self.rec_state == "close_btn":
                    self._capture_btn_template(x, y, "close_popup")
                    self._log(f"  ✓ 关闭弹窗 ({x},{y})")
                else:
                    self._log(f"  ✓ {self.rec_state} ({x},{y})")
                self._next_rec()
            elif key == keyboard.Key.f9 and self.rec_state in ["popup_close", "draft_close"]:
                self.coords[self.rec_state] = None
                self._log(f"  ✓ 跳过 {self.rec_state}")
                self._next_rec()
    
    def _next_rec(self):
        flow = {
            "export":      ("confirm",     "2/5 点导出呼出弹窗 → 鼠标在【确认导出】上 → F8"),
            "confirm":     ("popup_close", "3/5 鼠标在【收起至首页(亮起后)】上 → F8 (跳过按F9)"),
            "popup_close": ("close_btn",   "4/5 鼠标在导出完成后的【关闭(X)】按钮上 → F8"),
            "close_btn":   ("draft_close", "5/5 鼠标在【关闭草稿/返回首页】按钮上 → F8 (跳过按F9)"),
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
        """等待导出完成，返回 'return_home' 或 'close' 表示检测到哪个按钮"""
        rh = self.coords.get('popup_close')       # 收起至首页坐标
        cb = self.coords.get('close_btn')         # 关闭弹窗坐标
        
        # 只等 0.5s，防止快速导出的草稿错过按钮
        self._log("  · 等待导出弹窗 (0.5s)...")
        time.sleep(0.5)
        if self.stop_flag: return None
        
        # 加载模板图片
        base = os.path.dirname(os.path.abspath(CFG_FILE)) or "."
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        tpl_rh = os.path.join(base, "tpl_return_home.png")        # 录制时截取
        tpl_rh_bundled = os.path.join(meipass, "btn_bright.png")  # 内置
        tpl_close = os.path.join(base, "tpl_close_popup.png")     # 录制时截取
        
        self._log("  · 等待按钮就绪 (支持自动降级识别)...")
        while not self.stop_flag:
            # ── 1. 优先检测【收起至首页】 ──
            if rh:
                x_rh, y_rh = rh
                # 1.1 OpenCV 模板匹配 (优先在坐标附近区域查找，再全屏查找)
                for tpl_path in [tpl_rh, tpl_rh_bundled]:
                    if not os.path.exists(tpl_path): continue
                    try:
                        rx = max(0, x_rh - 100); ry = max(0, y_rh - 60)
                        loc = pyautogui.locateOnScreen(tpl_path, region=(rx, ry, 200, 120), confidence=0.82)
                        if loc is not None:
                            self._log("  ✓ 检测到【收起至首页】(局部模板匹配)")
                            return "return_home"
                    except Exception: pass
                    try:
                        loc = pyautogui.locateOnScreen(tpl_path, confidence=0.82)
                        if loc is not None:
                            self._log("  ✓ 检测到【收起至首页】(全屏模板匹配)")
                            return "return_home"
                    except Exception: pass
                
                # 1.2 像素特征采样：亮青色背景 (R<80 且 G>130 且 B>130)
                for ox in [-25, -15, -5, 0, 5, 15, 25]:
                    for oy in [-10, -5, 0, 5, 10]:
                        try:
                            r, g, b = pyautogui.pixel(x_rh + ox, y_rh + oy)
                            if r < 80 and g > 130 and b > 130:
                                self._log("  ✓ 检测到【收起至首页】(亮青色背景特征)")
                                return "return_home"
                        except Exception: pass
            
            # ── 2. 检测【关闭】按钮 (快速导出/已完成时自动降级，或常规关闭模式) ──
            close_pos = cb or (rh if not self.coords.get('close_btn') else None)
            
            # 2.1 模板匹配
            if os.path.exists(tpl_close):
                try:
                    if close_pos:
                        rx = max(0, close_pos[0] - 100); ry = max(0, close_pos[1] - 60)
                        loc = pyautogui.locateOnScreen(tpl_close, region=(rx, ry, 200, 120), confidence=0.82)
                        if loc is not None:
                            self._log("  ✓ 检测到【关闭】按钮 (局部模板匹配)")
                            return "close"
                except Exception: pass
                try:
                    loc = pyautogui.locateOnScreen(tpl_close, confidence=0.82)
                    if loc is not None:
                        self._log("  ✓ 检测到【关闭】按钮 (全屏模板匹配)")
                        return "close"
                except Exception: pass
            
            # 2.2 像素结构/对比度保底识别：检测 close_pos 区域是否有按钮文字/高对比度结构
            if close_pos:
                cx, cy = close_pos
                samples = []
                for ox in [-20, -10, 0, 10, 20]:
                    for oy in [-8, -4, 0, 4, 8]:
                        try:
                            samples.append(pyautogui.pixel(cx + ox, cy + oy))
                        except Exception: pass
                if samples:
                    max_b = max(max(r, g, b) for r, g, b in samples)
                    min_b = min(min(r, g, b) for r, g, b in samples)
                    contrast = max_b - min_b
                    # 导出未完成时底色平坦暗灰 (max_b<60, contrast<20)
                    # 导出完成显示按钮时有文字或边框 (max_b>160, contrast>60)，绝不提前误触
                    if max_b > 160 and contrast > 60:
                        self._log(f"  ✓ 检测到【关闭】按钮 (结构对比度)")
                        return "close"
                    
            time.sleep(1)
        return None

    def _start_export(self):
        req = [self.coords.get("export"), self.coords.get("confirm")]
        if None in req or (not self.coords.get("popup_close") and not self.coords.get("close_btn")):
            self._show_toast("未就绪", "请先完成坐标录制 (至少包含导出、确认与关闭/收起)", C_WARN); return
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
                self._log("  · 加载草稿 (8s)")
                for _ in range(8): self._chk(); time.sleep(1)
                
                self._chk(); pyautogui.click(*self.coords['export'])
                self._log("  · 点击导出")
                for _ in range(3): self._chk(); time.sleep(1)
                
                self._chk(); pyautogui.click(*self.coords['confirm'])
                self._log("  · 确认导出")
                time.sleep(1)
                pyautogui.press('enter')
                
                # 等待导出完成，智能检测哪个按钮出现
                result = self._wait_done()
                self._chk()
                
                if result == "return_home":
                    # 收起至首页按钮出现 → 点击它，直接回到首页
                    pyautogui.click(*self.coords['popup_close'])
                    self._log("  · 已收起至首页 (后台导出)")
                    time.sleep(1)
                elif result == "close":
                    # 关闭弹窗按钮出现（快速导出降级或常规模式）→ 点关闭，再点关闭草稿
                    cb = self.coords.get('close_btn') or self.coords.get('popup_close')
                    if cb:
                        pyautogui.click(*cb)
                        self._log("  · 点击关闭弹窗")
                    for _ in range(2): self._chk(); time.sleep(1)
                    dc = self.coords.get('draft_close')
                    if dc:
                        self._chk(); pyautogui.click(*dc)
                        self._log("  · 关闭草稿 → 返回首页")
                else:
                    # 异常或终止，尝试点击
                    cb = self.coords.get('popup_close') or self.coords.get('close_btn')
                    if cb: pyautogui.click(*cb)
                
                self._qst(i, "✅")
                self._log("  · 休息 1s")
                for _ in range(2): self._chk(); time.sleep(0.5)
            
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
            self._ask_yes_no("安全确认", "确定执行？此操作将提取 SRT 并隐藏字幕轨道", lambda: threading.Thread(target=self._extract_subs, daemon=True).start())
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
        if self._jianying_is_running():
            self._log("✗ 请先完全退出剪映，再执行解除隐藏")
            self.after(0, lambda: self._show_toast("剪映仍在运行", "请完全退出剪映后重试", C_WARN, 6000))
            return
        base = self.draft_dir.get()
        if not os.path.exists(base): self._log("✗ 草稿根目录不存在"); return
        items = []
        for fn in os.listdir(base):
            fp = os.path.join(base, fn)
            if os.path.isdir(fp):
                items.append((fn, fp))
        ok = 0
        for fn, draft_dir in items:
            try:
                subtitle_jsons, _ = self._find_subtitle_jsons(draft_dir)
                if not subtitle_jsons:
                    continue
                track_copies = 0
                for _, data, _ in subtitle_jsons:
                    track_copies += self._set_text_tracks_hidden(data, False)
                backups = self._save_subtitle_jsons(subtitle_jsons)
                self._log(
                    f"  ✓ {fn} 已解除隐藏 "
                    f"(同步 {len(subtitle_jsons)} 份，恢复 {track_copies} 条文字轨道，"
                    f"备份 {len(backups)} 份)"
                )
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

    def _load_plain_draft_json(self, path):
        """读取未加密的草稿 JSON；新版编码文件返回 None。"""
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _jianying_is_running():
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/NH"],
                capture_output=True, text=True, errors="ignore",
                creationflags=0x08000000, timeout=5
            )
            return "JianyingPro.exe" in result.stdout
        except Exception:
            return False

    def _encrypted_draft_io(self):
        draft_io = getattr(self, "_jianying_draft_io", None)
        if draft_io is None:
            draft_io = JianyingDraftIO()
            self._jianying_draft_io = draft_io
            self._log(f"  · 已加载剪映草稿接口: {draft_io.dll_path}")
        return draft_io

    def _find_subtitle_jsons(self, draft_dir):
        """读取根草稿和多时间线，支持明文及新版剪映加密格式。"""
        candidates = [os.path.join(draft_dir, 'draft_content.json')]
        timelines_dir = os.path.join(draft_dir, 'Timelines')
        if os.path.isdir(timelines_dir):
            for timeline_id in sorted(os.listdir(timelines_dir)):
                timeline_dir = os.path.join(timelines_dir, timeline_id)
                if not os.path.isdir(timeline_dir):
                    continue
                candidates.append(os.path.join(timeline_dir, 'draft_content.json'))

        subtitle_jsons, encoded_drafts, load_errors, seen = [], [], [], set()
        for path in candidates:
            norm = os.path.normcase(os.path.abspath(path))
            if norm in seen or not os.path.isfile(path):
                continue
            seen.add(norm)
            data = self._load_plain_draft_json(path)
            storage = "plain"
            if data is None:
                try:
                    data = self._encrypted_draft_io().read_json(path)
                    storage = "encrypted"
                    encoded_drafts.append(path)
                except Exception as exc:
                    load_errors.append(f"{path}: {exc}")
                    continue
            text_tracks = [t for t in data.get('tracks', []) if t.get('type') == 'text']
            if text_tracks and data.get('materials', {}).get('texts'):
                subtitle_jsons.append((path, data, storage))
        if not subtitle_jsons and load_errors:
            raise RuntimeError("；".join(load_errors))
        return subtitle_jsons, encoded_drafts

    def _subtitle_lines(self, data):
        texts = data.get('materials', {}).get('texts', [])
        text_by_id = {t.get('id'): t for t in texts if t.get('id')}
        segments = []
        for track in data.get('tracks', []):
            if track.get('type') == 'text':
                segments.extend(track.get('segments', []))
        segments.sort(key=lambda x: x.get('target_timerange', {}).get('start', 0))

        lines = []
        for segment in segments:
            material = text_by_id.get(segment.get('material_id'))
            if not material:
                continue
            content = material.get('content', {})
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    content = {'text': content}
            text = content.get('text', '') if isinstance(content, dict) else ''
            text = text or material.get('recognize_text', '') or material.get('base_content', '')
            if not isinstance(text, str) or not text.strip():
                continue
            timerange = segment.get('target_timerange', {})
            start = int(timerange.get('start', 0))
            duration = int(timerange.get('duration', 0))
            lines.extend([
                str(len(lines) // 4 + 1),
                f"{fmt_time(start)} --> {fmt_time(start + duration)}",
                text.strip(),
                "",
            ])
        return lines

    def _set_text_tracks_hidden(self, data, hidden=True):
        """切换文字轨道可见状态，保留轨道、片段和字幕素材。"""
        text_tracks = [t for t in data.get('tracks', []) if t.get('type') == 'text']
        for track in text_tracks:
            for segment in track.get('segments', []):
                segment['visible'] = not hidden
        return len(text_tracks)

    def _save_subtitle_jsons(self, subtitle_jsons):
        """先备份全部目标，再原子写入；任一写入失败则恢复所有文件。"""
        stamp = time.strftime('%Y%m%d_%H%M%S') + f"_{int(time.time() * 1000) % 1000:03d}"
        backups = []
        try:
            for path, _, _ in subtitle_jsons:
                backup = f"{path}.before_subtitle_{stamp}.bak"
                shutil.copy2(path, backup)
                backups.append((path, backup))
            for path, data, storage in subtitle_jsons:
                if storage == "encrypted":
                    self._encrypted_draft_io().write_json(path, data)
                    verified = self._encrypted_draft_io().read_json(path)
                else:
                    temp_path = f"{path}.subtitle.tmp"
                    try:
                        with open(temp_path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
                        os.replace(temp_path, path)
                    finally:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                    verified = self._load_plain_draft_json(path)
                if verified != data:
                    raise RuntimeError(f"写入后校验不一致，已回滚: {path}")
        except Exception:
            for path, backup in backups:
                if os.path.exists(backup):
                    shutil.copy2(backup, path)
            raise
        return [backup for _, backup in backups]

    def _extract_subs(self):
        self._log("提取字幕...")
        if self._jianying_is_running():
            self._log("✗ 请先完全退出剪映，再提取并隐藏字幕")
            self.after(0, lambda: self._show_toast("剪映仍在运行", "请完全退出剪映后重试", C_WARN, 6000))
            return
        base = self.draft_dir.get()
        if not os.path.exists(base):
            self._log("✗ 目录不存在")
            return
        out = os.path.join(os.path.expanduser("~"), "Desktop", "srt")
        os.makedirs(out, exist_ok=True)

        items = []
        for name in os.listdir(base):
            draft_dir = os.path.join(base, name)
            if os.path.isdir(draft_dir) and name != 'srt':
                items.append((name, draft_dir))

        ok = 0
        failed = 0
        for name, draft_dir in items:
            try:
                subtitle_jsons, encoded_drafts = self._find_subtitle_jsons(draft_dir)
                if not subtitle_jsons:
                    continue
                if encoded_drafts:
                    self._log(f"  · {name} 已通过剪映接口解密 {len(encoded_drafts)} 份 draft_content.json")

                # 根目录与 Timelines 中可能有镜像，只从第一份提取一次 SRT。
                lines = self._subtitle_lines(subtitle_jsons[0][1])
                if not lines:
                    raise ValueError("检测到文字轨道，但没有可提取的字幕文本")

                srt_path = os.path.join(out, f"{name}.srt")
                srt_temp = f"{srt_path}.tmp"
                with open(srt_temp, 'w', encoding='utf-8') as f:
                    f.write("\n".join(lines))
                os.replace(srt_temp, srt_path)

                hidden_track_copies = 0
                for _, data, _ in subtitle_jsons:
                    hidden_track_copies += self._set_text_tracks_hidden(data, True)
                backups = self._save_subtitle_jsons(subtitle_jsons)

                self._log(
                    f"  ✓ {name} → {os.path.basename(srt_path)} "
                    f"(同步 {len(subtitle_jsons)} 份，隐藏 {hidden_track_copies} 条文字轨道副本，"
                    f"备份 {len(backups)} 份)"
                )
                ok += 1
            except Exception as e:
                self._log(f"  ✗ {name}: {e}")
                failed += 1
        self._log(f"完成，成功提取并隐藏 {ok} 个，失败 {failed} 个")
        if failed:
            self.after(0, lambda: self._show_toast(
                "部分处理失败", f"成功 {ok} 个，失败 {failed} 个\n请查看日志，失败草稿已自动回滚", C_WARN, 8000
            ))
        else:
            self.after(0, lambda: self._show_toast(
                "提取完成", f"共处理 {ok} 个草稿\n字幕已保存至桌面/srt，原轨道已隐藏", C_SUCCESS, 6000
            ))

    # ═══════════════════════ 重命名 ═══════════════════════
    def _ren_preview_thread(self):
        mode = self.ren_mode_seg.get() if hasattr(self, "ren_mode_seg") else "正则匹配重命名"
        if mode == "AI 智能重命名":
            self._ai_preview_thread()
        else:
            self._regex_preview_thread()

    def _regex_preview_thread(self):
        folder = self.rename_folder_entry.get().strip().strip('"')
        template = self.fmt_entry.get().strip()
        if not folder:
            self._show_toast("提示", "请先选择目标文件夹", C_WARN)
            return
        if not os.path.isdir(folder):
            self._show_toast("路径错误", "目标文件夹不存在", C_WARN)
            return
        parsed = _rename_parse_template(template)
        if not parsed:
            self._show_toast("模板错误", "模板必须包含一个数字，例如：短剧名称_第1集", C_WARN)
            return

        prefix, suffix = parsed
        self.rename_folder = folder
        plan, skipped_duplicates, skipped_without_number = _rename_plan(
            folder, prefix, suffix
        )
        for item in self.ren_tree.get_children():
            self.ren_tree.delete(item)

        self.rename_plan = []
        for old_name, new_name in plan:
            source = os.path.join(folder, old_name)
            target = os.path.join(folder, new_name)
            self.rename_plan.append({
                "source": source,
                "target": target,
                "old_name": old_name,
                "new_name": new_name,
                "mode": "正则匹配"
            })
            self.ren_tree.insert("", "end", values=(old_name, new_name, "正则匹配"))

        if skipped_duplicates:
            self._log("【正则匹配】检测到重复集数，以下文件跳过：")
            for old_name, target in skipped_duplicates:
                self._log(f"  {old_name} -> {target}")
        if skipped_without_number:
            self._log("【正则匹配】以下文件名没有识别到集数，已跳过：")
            for filename in skipped_without_number:
                self._log(f"  {filename}")
        if not self.rename_plan:
            self.ren_btn.configure(state="disabled")
            self._show_toast("没有可改名文件", "【正则匹配】没有生成有效的重命名计划", C_WARN)
            return

        self.ren_btn.configure(state="normal")
        self._log(f"✓ 【正则匹配重命名】预览完成，共 {len(self.rename_plan)} 个文件")

    def _ai_preview_thread(self):
        if not getattr(self, "sel_files", None):
            folder = self.rename_folder_entry.get().strip().strip('"')
            if folder and os.path.isdir(folder):
                self._load_folder_for_ai(folder)
            if not getattr(self, "sel_files", None):
                self._show_toast("提示", "请先选择视频/字幕文件或载入文件夹", C_WARN)
                return
        self.ai_preview_btn.configure(state="disabled", text="⏳ AI 正在思考中...")
        self.ren_progress.pack(fill="x", padx=12, pady=(0, 4))
        self.ren_progress.start()
        threading.Thread(target=self._ai_preview, daemon=True).start()

    def _ai_preview(self):
        self._log("【AI 智能重命名】开始分析...")
        fns = [os.path.basename(f) for f in self.sel_files]
        fmt = self.ai_fmt_entry.get().strip() or "短剧名称_第X集"
        prompt = (f"Rename these files to: {fmt}\n"
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
            self.after(0, self._fill_ai_ren, data)
        except Exception as e: 
            msg = self._get_api_err_msg(e)
            self._log(f"✗ 【AI 重命名失败】{msg}")
            self.after(0, lambda m=msg: self._show_toast("AI 重命名失败", m, C_DANGER, 6000))
            self.after(0, self._reset_ai_ren_ui)

    def _reset_ai_ren_ui(self):
        self.ai_preview_btn.configure(state="normal", text="✨  AI 预览重命名 [AI 智能]")
        self.ren_progress.stop()
        self.ren_progress.pack_forget()

    def _fill_ai_ren(self, data):
        self._reset_ai_ren_ui()
        for i in self.ren_tree.get_children():
            self.ren_tree.delete(i)
        dm = {os.path.basename(f): os.path.dirname(f) for f in self.sel_files}
        self.rename_plan = []
        for it in data:
            o, n = it.get("old"), it.get("new")
            if o and n and o in dm:
                d = dm[o]
                source = os.path.join(d, o)
                target = os.path.join(d, n)
                self.rename_plan.append({
                    "source": source,
                    "target": target,
                    "old_name": o,
                    "new_name": n,
                    "mode": "AI重命名"
                })
                self.ren_tree.insert("", "end", values=(o, n, "AI重命名"))
        if self.rename_plan:
            self.ren_btn.configure(state="normal")
            self._log(f"✓ 【AI 智能重命名】预览已生成，共 {len(self.rename_plan)} 个文件")
        else:
            self.ren_btn.configure(state="disabled")
            self._show_toast("AI 未返回匹配文件", "未能成功生成匹配规则，请检查格式或文件列表", C_WARN)

    def _exec_rename(self):
        if not self.rename_plan:
            return
        if not self._ask_rename_confirmation():
            return
        ok = 0
        skipped = 0
        errors = 0
        total = len(self.rename_plan)
        current_mode = self.ren_mode_seg.get() if hasattr(self, "ren_mode_seg") else "重命名"

        for item in self.rename_plan:
            source = item["source"]
            target = item["target"]
            old_name = item["old_name"]
            new_name = item["new_name"]
            if source == target or old_name == new_name:
                continue
            if os.path.exists(target):
                skipped += 1
                self._log(f"  跳过，目标已存在：{new_name}")
                continue
            try:
                os.rename(source, target)
                ok += 1
            except OSError as exc:
                errors += 1
                self._log(f"  ✗ {old_name}: {exc}")

        self._show_toast(
            f"【{current_mode}】完成" if not errors else f"【{current_mode}】部分完成",
            f"成功 {ok}/{total} 个，已存在 {skipped} 个，失败 {errors} 个",
            C_SUCCESS if not errors else C_WARN,
        )
        self.rename_plan = []
        self.sel_files = []
        if hasattr(self, "file_lbl"):
            self.file_lbl.configure(text="未选择")
        for i in self.ren_tree.get_children(): self.ren_tree.delete(i)
        self.ren_btn.configure(state="disabled")

    def _ask_rename_confirmation(self):
        """执行前复用应用确认框，避免预览后误改文件。"""
        result = {"confirmed": False}
        current_mode = self.ren_mode_seg.get() if hasattr(self, "ren_mode_seg") else "重命名"
        dialog = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12, border_width=2, border_color=C_BORDER)
        dialog.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(dialog, text=f"确认【{current_mode}】", font=("Segoe UI", 14, "bold"), text_color=C_TEXT).pack(pady=(20, 10))
        ctk.CTkLabel(
            dialog,
            text=f"将使用【{current_mode}】修改 {len(self.rename_plan)} 个文件，是否继续？",
            font=("Segoe UI", 12), text_color=C_TEXT,
        ).pack(pady=(0, 20), padx=24)
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 20))

        def close(confirmed):
            result["confirmed"] = confirmed
            dialog.destroy()

        ctk.CTkButton(buttons, text="取消", width=80, command=lambda: close(False)).pack(side="left", padx=4)
        ctk.CTkButton(buttons, text="确认", width=80, fg_color=C_PRIMARY, command=lambda: close(True)).pack(side="right", padx=4)
        self.wait_window(dialog)
        return result["confirmed"]

# ─── 启动 ──────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
