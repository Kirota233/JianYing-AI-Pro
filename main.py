import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading
import sys
import time
import os
import re
import json
import pyautogui
import requests
import subprocess
from PIL import Image
from pynput import keyboard
from google import genai
from google.genai import types
import ttkbootstrap as tb
from ttkbootstrap.constants import *

# ==========================================
# 授权控制与自动更新模块
# ==========================================
AUTH_URL = "https://raw.githubusercontent.com/Kirota233/JianYing-AI-Pro/master/auth.json"
APP_VERSION = "1.0.3"

def check_authorization():
    if "placeholder" in AUTH_URL:
        return 
    try:
        resp = requests.get(AUTH_URL, timeout=5)
        data = resp.json()
        status = data.get("status", "blocked")
        if status == "destroy":
            self_destruct()
        elif status == "blocked":
            tk.Tk().withdraw()
            messagebox.showerror("授权失败", "该软件未获授权或授权已过期。")
            sys.exit(0)
        remote_version = data.get("version", APP_VERSION)
        update_url = data.get("update_url", "")
        if remote_version != APP_VERSION and update_url:
            root = tk.Tk(); root.withdraw()
            if messagebox.askyesno("发现新版本", f"v{remote_version} 可用 (当前 v{APP_VERSION})，是否更新？"):
                perform_update(update_url)
            root.destroy()
    except: pass

def perform_update(update_url):
    import urllib.request
    root = tk.Tk(); root.withdraw()
    messagebox.showinfo("更新中", "正在下载，完成后自动重启...")
    try:
        exe_path = os.path.abspath(sys.argv[0])
        if not exe_path.endswith('.exe'):
            messagebox.showwarning("提示", "源码模式下不支持自动更新。"); return
        new_exe_path = exe_path + ".new"
        urllib.request.urlretrieve(update_url, new_exe_path)
        bat_path = os.path.join(os.environ['TEMP'], "update_app.bat")
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(f'@echo off\nping 127.0.0.1 -n 4 > nul\ndel "{exe_path}" /f /q\nmove /y "{new_exe_path}" "{exe_path}"\nstart "" "{exe_path}"\ndel "%~f0" /f /q\n')
        subprocess.Popen(bat_path, creationflags=subprocess.CREATE_NO_WINDOW)
        sys.exit(0)
    except Exception as e:
        messagebox.showerror("更新失败", str(e)); sys.exit(0)

def self_destruct():
    exe_path = os.path.abspath(sys.argv[0])
    bat_path = os.path.join(os.environ['TEMP'], "seppuku.bat")
    with open(bat_path, "w", encoding='utf-8') as f:
        f.write('@echo off\nping 127.0.0.1 -n 3 > nul\n')
        if os.path.exists("config.json"): f.write('del "config.json" /f /q\n')
        if exe_path.endswith('.exe'): f.write(f'del "{exe_path}" /f /q\n')
        f.write('del "%~f0" /f /q\n')
    subprocess.Popen(bat_path, creationflags=subprocess.CREATE_NO_WINDOW)
    sys.exit(0)

# ==========================================
CONFIG_FILE = "config.json"
DEFAULT_API_KEY = "AIzaSyDQ4s-9ynGQcJw6oNDF5G2fNewnuF1zkaY"

def format_time(t_us):
    t_ms = int(t_us) // 1000
    ms = t_ms % 1000
    s = (t_ms // 1000) % 60
    m = (t_ms // (1000 * 60)) % 60
    h = (t_ms // (1000 * 60 * 60))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

class LogRedirector:
    """将 print 输出重定向到 Text 控件，同时保持格式统一"""
    def __init__(self, text_widget):
        self.widget = text_widget
    def write(self, msg):
        self.widget.configure(state="normal")
        self.widget.insert(tk.END, msg)
        self.widget.see(tk.END)
        self.widget.configure(state="disabled")
    def flush(self): pass

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"剪映 AI 极速群导  v{APP_VERSION}")
        self.root.geometry("460x640")
        self.root.attributes('-topmost', True)
        self.root.resizable(False, False)
        
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        self.root.geometry(f"+{sw - 480}+{sh - 690}")
        
        self.coords = {"export": None, "confirm": None, "popup_close": None, "draft_close": None}
        self.close_btn_color = None
        self.stop_flag = False
        self.recording_state = None
        self.api_key = DEFAULT_API_KEY
        self.rename_map = []
        self.drafts_list = []       # [(x, y, name), ...]
        self.selected_files = []
        
        self.load_config()
        self.build_ui()
        self.show_cloud_warning()
        
        self.listener = keyboard.Listener(on_release=self.on_key_release)
        self.listener.start()

    def get_api_client(self):
        key = self.api_key_var.get().strip()
        if not key: key = DEFAULT_API_KEY
        return genai.Client(api_key=key)

    def show_cloud_warning(self):
        messagebox.showwarning("使用须知",
            "⚠️ 重要提示\n\n"
            "• 需要自动导出的草稿必须从云空间下载到本地\n"
            "• 不能提前双击打开草稿，否则会导致提取失败\n"
            "• 录制坐标时请用一个废弃草稿操作")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.coords = data.get("coords", self.coords)
                    self.close_btn_color = tuple(data.get("color", [])) if data.get("color") else None
                    self.api_key = data.get("api_key", DEFAULT_API_KEY)
            except: pass

    def save_config(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump({"coords": self.coords, "color": self.close_btn_color,
                       "api_key": self.api_key_var.get().strip()}, f)

    # ======================== UI ========================
    def build_ui(self):
        nb = tb.Notebook(self.root, bootstyle="info")
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        
        self.tab1 = tb.Frame(nb)
        self.tab2 = tb.Frame(nb)
        self.tab3 = tb.Frame(nb)
        self.tab4 = tb.Frame(nb)
        nb.add(self.tab1, text="  导出  ")
        nb.add(self.tab2, text="  字幕  ")
        nb.add(self.tab3, text="  重命名  ")
        nb.add(self.tab4, text="  设置  ")
        
        self._build_tab_export()
        self._build_tab_subtitle()
        self._build_tab_rename()
        self._build_tab_settings()
        
        # 底部状态栏
        sf = tb.Frame(self.root)
        sf.pack(fill="x", padx=10, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tb.Label(sf, textvariable=self.status_var, font=("Segoe UI", 8),
                 foreground="#888").pack(side="left")
        tb.Label(sf, text=f"v{APP_VERSION}", font=("Segoe UI", 8),
                 foreground="#bbb").pack(side="right")

    def _build_tab_export(self):
        # --- 坐标录制 ---
        cf = tb.LabelFrame(self.tab1, text="坐标录制", padding=8, bootstyle="secondary")
        cf.pack(fill="x", padx=8, pady=(8, 4))
        
        row = tb.Frame(cf)
        row.pack(fill="x")
        self.lbl_guide = tb.Label(row, text="未录制", font=("Segoe UI", 9))
        self.lbl_guide.pack(side="left", fill="x", expand=True)
        tb.Button(row, text="引导录制 (F8)", bootstyle="info-outline",
                  command=self.start_wizard, width=16).pack(side="right")
        
        if self.coords.get('export'):
            self.lbl_guide.config(text="✅ 坐标已就绪", foreground="#28a745")

        # --- 任务列表 ---
        tf = tb.LabelFrame(self.tab1, text="导出队列", padding=8, bootstyle="secondary")
        tf.pack(fill="both", expand=True, padx=8, pady=4)
        
        cols = ("序号", "草稿名称", "状态")
        self.task_tree = ttk.Treeview(tf, columns=cols, show="headings", height=5, selectmode="browse")
        self.task_tree.heading("序号", text="#")
        self.task_tree.heading("草稿名称", text="草稿名称")
        self.task_tree.heading("状态", text="状态")
        self.task_tree.column("序号", width=36, anchor="center")
        self.task_tree.column("草稿名称", width=230)
        self.task_tree.column("状态", width=80, anchor="center")
        
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=sb.set)
        self.task_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        
        tb.Button(tf, text="📸 AI 扫描首页", bootstyle="info-outline",
                  command=self.scan_drafts_thread).pack(fill="x", pady=(6, 0))
        
        # --- 控制 ---
        bf = tb.Frame(self.tab1)
        bf.pack(fill="x", padx=8, pady=6)
        self.start_btn = tb.Button(bf, text="▶  从选中项开始导出", bootstyle="success",
                                   command=self.start_task)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4), ipady=5)
        self.stop_btn = tb.Button(bf, text="⏹  中断", bootstyle="danger-outline",
                                  command=self.stop_task)
        self.stop_btn.pack(side="right", ipady=5, ipadx=10)
        
        # --- 日志 ---
        lf = tb.LabelFrame(self.tab1, text="运行日志", padding=4, bootstyle="secondary")
        lf.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.log_area = tk.Text(lf, height=5, font=("Consolas", 8),
                                bg="#f8f9fa", fg="#212529", bd=0, wrap="word",
                                state="disabled", relief="flat")
        log_sb = ttk.Scrollbar(lf, orient="vertical", command=self.log_area.yview)
        self.log_area.configure(yscrollcommand=log_sb.set)
        self.log_area.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")
        sys.stdout = LogRedirector(self.log_area)

    def _build_tab_subtitle(self):
        pf = tb.LabelFrame(self.tab2, text="草稿目录", padding=10, bootstyle="secondary")
        pf.pack(fill="x", padx=8, pady=8)
        pi = tb.Frame(pf)
        pi.pack(fill="x")
        self.draft_path_var = tk.StringVar(value=r"D:\JianyingPro Drafts")
        tb.Entry(pi, textvariable=self.draft_path_var).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tb.Button(pi, text="...", width=3, command=self.browse_draft_path,
                  bootstyle="secondary-outline").pack(side="right")
        
        tb.Button(self.tab2, text="📥  提取字幕并删除轨道 → 桌面/srt",
                  bootstyle="warning", command=self.extract_subtitles_thread
                  ).pack(fill="x", padx=8, pady=10, ipady=6)
        
        info = tb.Frame(self.tab2)
        info.pack(fill="x", padx=12)
        for t in ["提取 draft_content.json 中的文本为 SRT",
                   "设置文本轨道 flag=2 实现无字幕版",
                   "按集数命名保存到 桌面/srt 文件夹"]:
            tb.Label(info, text=f"•  {t}", foreground="#6c757d", font=("Segoe UI", 9)).pack(anchor="w", pady=1)

    def browse_draft_path(self):
        f = filedialog.askdirectory(initialdir=self.draft_path_var.get())
        if f: self.draft_path_var.set(f)

    def _build_tab_rename(self):
        ff = tb.LabelFrame(self.tab3, text="文件选择", padding=8, bootstyle="secondary")
        ff.pack(fill="x", padx=8, pady=(8, 4))
        row = tb.Frame(ff)
        row.pack(fill="x")
        tb.Button(row, text="📁 选择文件", command=self.select_videos,
                  bootstyle="secondary-outline", width=12).pack(side="left")
        self.lbl_file_count = tb.Label(row, text="未选择", font=("Segoe UI", 9), foreground="#888")
        self.lbl_file_count.pack(side="left", padx=8)
        
        pf = tb.LabelFrame(self.tab3, text="命名格式", padding=8, bootstyle="secondary")
        pf.pack(fill="x", padx=8, pady=4)
        self.rename_prompt = tb.Entry(pf)
        self.rename_prompt.insert(0, "短剧名称_第X集")
        self.rename_prompt.pack(fill="x")
        
        tb.Button(self.tab3, text="✨  AI 预览重命名", bootstyle="info-outline",
                  command=self.preview_rename_thread).pack(fill="x", padx=8, pady=4)
        
        cols = ("原名", "新名")
        self.ren_tree = ttk.Treeview(self.tab3, columns=cols, show="headings", height=5)
        self.ren_tree.heading("原名", text="原文件名")
        self.ren_tree.heading("新名", text="新文件名")
        self.ren_tree.column("原名", width=180)
        self.ren_tree.column("新名", width=180)
        self.ren_tree.pack(fill="both", expand=True, padx=8, pady=2)
        
        self.btn_exec_rename = tb.Button(self.tab3, text="✅  执行重命名", bootstyle="success",
                                         state="disabled", command=self.execute_rename)
        self.btn_exec_rename.pack(fill="x", padx=8, pady=6)

    def select_videos(self):
        files = filedialog.askopenfilenames(title="选择视频",
                    filetypes=[("视频", "*.mp4 *.mov *.avi *.mkv"), ("全部", "*.*")])
        if files:
            self.selected_files = list(files)
            self.lbl_file_count.config(text=f"{len(self.selected_files)} 个文件")
            self.btn_exec_rename.config(state="disabled")
            for i in self.ren_tree.get_children(): self.ren_tree.delete(i)

    def preview_rename_thread(self):
        if not self.selected_files:
            messagebox.showwarning("提示", "请先选择文件"); return
        threading.Thread(target=self._preview_rename, daemon=True).start()

    def _preview_rename(self):
        print("AI 分析重命名...")
        fns = [os.path.basename(f) for f in self.selected_files]
        prompt = (f"Rename these video files to format: {self.rename_prompt.get()}\n"
                  f"Files:\n{json.dumps(fns, ensure_ascii=False)}\n"
                  f"Return ONLY raw JSON array with 'old' and 'new' keys. No markdown.")
        try:
            r = self.get_api_client().models.generate_content(
                model='gemini-3.6-flash', contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1))
            t = r.text.strip()
            if t.startswith("```json"): t = t[7:]
            if t.startswith("```"): t = t[3:]
            if t.endswith("```"): t = t[:-3]
            data = json.loads(t.strip())
            self.root.after(0, self._fill_rename_tree, data)
        except Exception as e:
            print(f"❌ {e}")

    def _fill_rename_tree(self, data):
        for i in self.ren_tree.get_children(): self.ren_tree.delete(i)
        dm = {os.path.basename(f): os.path.dirname(f) for f in self.selected_files}
        self.rename_map = []
        for item in data:
            o, n = item.get("old"), item.get("new")
            if o and n and o in dm:
                d = dm[o]
                self.rename_map.append((os.path.join(d, o), os.path.join(d, n)))
                self.ren_tree.insert("", "end", values=(o, n))
        self.btn_exec_rename.config(state="normal")
        print("✅ 预览已生成")

    def execute_rename(self):
        if not self.rename_map: return
        ok = 0
        for o, n in self.rename_map:
            try: os.rename(o, n); ok += 1
            except Exception as e: print(f"❌ {os.path.basename(o)}: {e}")
        messagebox.showinfo("完成", f"成功重命名 {ok}/{len(self.rename_map)} 个文件")
        self.selected_files = []; self.lbl_file_count.config(text="未选择")
        for i in self.ren_tree.get_children(): self.ren_tree.delete(i)
        self.btn_exec_rename.config(state="disabled")

    def _build_tab_settings(self):
        af = tb.LabelFrame(self.tab4, text="API Key", padding=10, bootstyle="secondary")
        af.pack(fill="x", padx=8, pady=8)
        tb.Label(af, text="默认为作者公用 Key，有速率限制。\n建议到 Google AI Studio 免费申请自己的。",
                 foreground="#dc3545", font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=4)
        self.api_key_var = tk.StringVar(value=self.api_key)
        tb.Entry(af, textvariable=self.api_key_var, show="*").pack(fill="x", pady=4)
        tb.Button(af, text="保存", bootstyle="success-outline",
                  command=lambda: (self.save_config(), messagebox.showinfo("✓", "已保存"))
                  ).pack(anchor="e")

    # ======================== 字幕提取 ========================
    def extract_subtitles_thread(self):
        if not messagebox.askyesno("确认", "草稿全部来自云空间且未打开过？"): return
        threading.Thread(target=self._extract_subs, daemon=True).start()

    def _extract_subs(self):
        print("提取字幕轨道...")
        base = self.draft_path_var.get()
        if not os.path.exists(base): print(f"❌ 目录不存在"); return
        out = os.path.join(os.path.expanduser("~"), "Desktop", "srt")
        os.makedirs(out, exist_ok=True)
        
        items = []
        for fn in os.listdir(base):
            fp = os.path.join(base, fn)
            if not os.path.isdir(fp) or fn == 'srt': continue
            jp = os.path.join(fp, 'draft_content.json')
            if not os.path.exists(jp): continue
            ep = None
            ip = os.path.join(fp, 'draft_info.json')
            if os.path.exists(ip):
                try:
                    with open(ip, 'r', encoding='utf-8') as f:
                        m = re.search(r'\d+', json.load(f).get('draft_name', ''))
                        if m: ep = int(m.group())
                except: pass
            if ep is None:
                m = re.search(r'\d+', fn)
                ep = int(m.group()) if m else 999999
            items.append({"jp": jp, "ep": ep, "fn": fn})
        items.sort(key=lambda x: x["ep"])
        
        cnt, ok = 1, 0
        for d in items:
            try:
                with open(d["jp"], 'r', encoding='utf-8') as f: data = json.load(f)
                tdict = {t['id']: t for t in data.get('materials', {}).get('texts', [])}
                tracks = [t for t in data.get('tracks', []) if t.get('type') == 'text']
                if not tracks: continue
                segs = []
                for tr in tracks:
                    tr['flag'] = 2
                    segs.extend(tr.get('segments', []))
                segs.sort(key=lambda x: x.get('target_timerange', {}).get('start', 0))
                lines, idx = [], 1
                for seg in segs:
                    tm = tdict.get(seg.get('material_id'))
                    if not tm: continue
                    try: txt = json.loads(tm.get('content', '{}')).get('text', '')
                    except: txt = tm.get('content', '')
                    if not txt.strip(): continue
                    tr = seg.get('target_timerange', {})
                    s = tr.get('start', 0)
                    lines += [str(idx), f"{format_time(s)} --> {format_time(s+tr.get('duration',0))}", txt.strip(), ""]
                    idx += 1
                if lines:
                    ep = d["ep"] if d["ep"] != 999999 else cnt
                    sp = os.path.join(out, f"{ep}.srt")
                    if os.path.exists(sp): sp = os.path.join(out, f"{ep}_{cnt}.srt")
                    with open(sp, 'w', encoding='utf-8') as f: f.write("\n".join(lines))
                    with open(d["jp"], 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
                    print(f"  ✓ {d['fn']} → {os.path.basename(sp)}")
                    ok += 1; cnt += 1
            except Exception as e: print(f"  ✗ {d['fn']}: {e}")
        print(f"完成，共提取 {ok} 个")

    # ======================== 坐标向导 ========================
    def start_wizard(self):
        self.recording_state = "export"
        self.lbl_guide.config(text="[1/4] 鼠标放在【导出】按钮上，按 F8", foreground="#0d6efd")
        print("向导开始，请按 F8 录制各按钮位置")

    def on_key_release(self, key):
        if key == keyboard.Key.f8 and self.recording_state:
            x, y = pyautogui.position()
            steps = {
                "export": ("confirm", "[2/4] 手动点导出，鼠标放在蓝色【确认导出】上，按 F8",
                           f"  ✓ 导出按钮 ({x},{y})"),
                "confirm": ("popup_close", "[3/4] 手动确认导出，等完成后鼠标放在【关闭】上，按 F8",
                            f"  ✓ 确认按钮 ({x},{y})"),
                "popup_close": ("draft_close", "[4/4] 点掉弹窗，鼠标放在右上角【×】上，按 F8",
                                None),
                "draft_close": (None, "✅ 录制完成", f"  ✓ 关闭草稿 ({x},{y})")
            }
            
            state = self.recording_state
            self.coords[state] = (x, y)
            
            if state == "popup_close":
                r, g, b = pyautogui.pixel(x, y)
                self.close_btn_color = (r, g, b)
                print(f"  ✓ 弹窗关闭 ({x},{y}) 颜色 RGB({r},{g},{b})")
            elif steps[state][2]:
                print(steps[state][2])
            
            next_state, label_text, _ = steps[state]
            self.recording_state = next_state
            
            if next_state is None:
                self.save_config()
                self.root.after(0, lambda: self.lbl_guide.config(text=label_text, foreground="#28a745"))
                print("向导完成！坐标已保存")
            else:
                self.root.after(0, lambda t=label_text: self.lbl_guide.config(text=t, foreground="#0d6efd"))

    # ======================== AI 扫描草稿 ========================
    def scan_drafts_thread(self):
        threading.Thread(target=self._scan_drafts, daemon=True).start()

    def _scan_drafts(self):
        print("截屏并发送给 AI 识别...")
        self.status_var.set("AI 扫描中...")
        pyautogui.screenshot("home_screen.png")
        client = self.get_api_client()
        
        try:
            image = Image.open("home_screen.png")
            prompt = (
                "You are analyzing a video editor (JianYing) home screen showing a grid of draft thumbnails.\n"
                "Find EVERY video draft thumbnail visible on screen.\n"
                "For each draft, return:\n"
                "1. The bounding box as [ymin, xmin, ymax, xmax] normalized to 1000\n"
                "2. The visible title/name text below or on the thumbnail\n\n"
                "Return ONLY a JSON array of objects with keys 'box' (array of 4 ints) and 'name' (string).\n"
                "Example: [{\"box\": [100, 50, 300, 250], \"name\": \"我的视频草稿\"}]\n"
                "No markdown formatting. Just raw JSON."
            )
            r = client.models.generate_content(
                model='gemini-3.6-flash', contents=[image, prompt],
                config=types.GenerateContentConfig(temperature=0.1))
            
            text = r.text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            
            data = json.loads(text.strip())
            
            sw, sh = pyautogui.size()
            raw = []
            for item in data:
                box = item.get("box", [])
                name = item.get("name", "未知草稿")
                if len(box) == 4:
                    ymin, xmin, ymax, xmax = box
                    cx = int((xmin+xmax)/2/1000*sw)
                    cy = int((ymin+ymax)/2/1000*sh)
                    raw.append((cx, cy, name))
            
            if not raw:
                print("⚠️ 未识别到草稿")
                self.status_var.set("未识别到草稿"); return
            
            # 排序：按 Y 分行，每行按 X 排
            raw.sort(key=lambda c: c[1])
            rows, cur = [], []
            for c in raw:
                if not cur: cur.append(c)
                else:
                    if abs(c[1] - cur[0][1]) < 100: cur.append(c)
                    else:
                        cur.sort(key=lambda i: i[0])
                        rows.append(cur); cur = [c]
            if cur:
                cur.sort(key=lambda i: i[0])
                rows.append(cur)
            
            self.drafts_list = []
            for row in rows: self.drafts_list.extend(row)
            
            self.root.after(0, self._fill_task_tree)
            names_preview = ", ".join([d[2] for d in self.drafts_list[:3]])
            if len(self.drafts_list) > 3: names_preview += "..."
            print(f"识别到 {len(self.drafts_list)} 个草稿: {names_preview}")
            self.status_var.set(f"{len(self.drafts_list)} 个草稿已就绪")
        except Exception as e:
            print(f"❌ {e}")
            self.status_var.set("扫描失败")

    def _fill_task_tree(self):
        for i in self.task_tree.get_children(): self.task_tree.delete(i)
        for i, (x, y, name) in enumerate(self.drafts_list):
            self.task_tree.insert("", "end", iid=str(i), values=(i+1, name, "待处理"))
        if self.drafts_list:
            self.task_tree.selection_set("0")

    # ======================== 导出核心 ========================
    def stop_task(self):
        self.stop_flag = True
        print("🛑 中断指令已接收")
        self.start_btn.config(state="normal")
        self.status_var.set("已中断")

    def wait_for_export_done(self):
        """
        回归简单可靠的方案：点击确认导出后先强制等待 5 秒
        让导出进度对话框完全出现，然后再用简单的像素颜色匹配
        等待关闭按钮出现（与之前第一集成功时的逻辑完全一致）。
        """
        x, y = self.coords['popup_close']
        tc = self.close_btn_color
        
        # 强制等待 5 秒，让导出进度条对话框完全渲染出来
        print("  · 等待进度条出现 (5s)...")
        for _ in range(5):
            if self.stop_flag: return
            time.sleep(1)
        
        # 简单轮询：等待像素颜色与目标匹配（关闭按钮出现 = 导出完成）
        print("  · 等待导出完成...")
        while not self.stop_flag:
            r, g, b = pyautogui.pixel(x, y)
            diff = abs(r-tc[0]) + abs(g-tc[1]) + abs(b-tc[2])
            if diff < 30:
                print("  ✓ 导出完成")
                break
            time.sleep(2)

    def start_task(self):
        if None in self.coords.values():
            messagebox.showerror("未就绪", "请先完成坐标录制向导"); return
        if not self.drafts_list:
            messagebox.showerror("未就绪", "请先点击【AI 扫描首页】"); return
        
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请在导出队列中选中一行作为起始点"); return
            
        start_idx = int(sel[0])
        start_name = self.drafts_list[start_idx][2]
        
        if not messagebox.askyesno("确认开始",
                f"将从第 {start_idx+1} 个草稿【{start_name}】开始导出，\n"
                f"共 {len(self.drafts_list) - start_idx} 个待处理。\n\n确认开始？"):
            return
        
        self.stop_flag = False
        self.start_btn.config(state="disabled")
        threading.Thread(target=self._run_export, args=(start_idx,), daemon=True).start()

    def check_stop(self):
        if self.stop_flag: raise Exception("用户中断")

    def _update_status(self, idx, status):
        self.root.after(0, lambda: self.task_tree.set(str(idx), "状态", status))

    def _run_export(self, start):
        try:
            total = len(self.drafts_list)
            count = total - start
            print(f"\n{'─'*36}")
            print(f"开始导出：第{start+1}集 → 第{total}集 (共{count}个)")
            
            for i in range(start, total):
                self.check_stop()
                dx, dy, name = self.drafts_list[i]
                self._update_status(i, "▶ 处理中")
                self.status_var.set(f"导出中  {i-start+1}/{count}")
                print(f"\n[{i+1}/{total}] {name}")
                
                # 1. 打开草稿
                pyautogui.click(dx, dy)
                print("  · 加载草稿 (7s)")
                for _ in range(7): self.check_stop(); time.sleep(1)
                
                # 2. 导出
                self.check_stop()
                pyautogui.click(*self.coords['export'])
                print("  · 点击导出")
                for _ in range(3): self.check_stop(); time.sleep(1)
                
                # 3. 确认
                self.check_stop()
                pyautogui.click(*self.coords['confirm'])
                print("  · 点击确认导出")
                
                # 4. 等待完成
                self.wait_for_export_done()
                self.check_stop()
                
                # 5. 关闭弹窗
                pyautogui.click(*self.coords['popup_close'])
                print("  · 关闭完成弹窗")
                for _ in range(3): self.check_stop(); time.sleep(1)
                
                # 6. 返回首页
                self.check_stop()
                pyautogui.click(*self.coords['draft_close'])
                print("  · 返回首页")
                
                self._update_status(i, "✅")
                
                print("  · 休息 5s")
                for _ in range(5): self.check_stop(); time.sleep(1)
                
            print(f"\n🎉 全部完成！共导出 {count} 个草稿")
            self.status_var.set(f"全部完成  共{count}个")
            
        except Exception as e:
            print(f"\n⏸ {e}")
        finally:
            self.root.after(0, lambda: self.start_btn.config(state="normal"))

if __name__ == "__main__":
    check_authorization()
    app = tb.Window("剪映 AI 极速群导", themename="cosmo")
    style = tb.Style()
    style.configure('.', font=('Segoe UI', 9))
    gui = App(app)
    app.mainloop()
