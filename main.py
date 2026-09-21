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
# 授权控制与自动更新模块 (Kill-Switch & Auto-Updater)
# ==========================================
AUTH_URL = "https://raw.githubusercontent.com/Kirota233/JianYing-AI-Pro/master/auth.json"
APP_VERSION = "1.0.2"

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
            messagebox.showerror("授权失败", "该软件未获授权或授权已过期，无法继续运行。")
            sys.exit(0)
            
        remote_version = data.get("version", APP_VERSION)
        update_url = data.get("update_url", "")
        if remote_version != APP_VERSION and update_url:
            root = tk.Tk()
            root.withdraw()
            if messagebox.askyesno("发现新版本", f"检测到新版本 v{remote_version} (当前版本 v{APP_VERSION})。\n\n是否立即进行自动更新？"):
                perform_update(update_url)
            root.destroy()
            
    except Exception as e:
        pass

def perform_update(update_url):
    import urllib.request
    
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("正在下载更新", "程序正在后台下载新版本，这可能需要几十秒钟。\n\n请点击【确定】并稍候，下载完成后软件将自动重启。")
    
    try:
        exe_path = os.path.abspath(sys.argv[0])
        if not exe_path.endswith('.exe'):
            messagebox.showwarning("更新提示", "当前处于 Python 源码运行模式，自动更新只在 EXE 打包版本中生效。")
            return
            
        new_exe_path = exe_path + ".new"
        urllib.request.urlretrieve(update_url, new_exe_path)
        
        bat_path = os.path.join(os.environ['TEMP'], "update_app.bat")
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write('@echo off\n')
            f.write('ping 127.0.0.1 -n 4 > nul\n')
            f.write(f'del "{exe_path}" /f /q\n')
            f.write(f'move /y "{new_exe_path}" "{exe_path}"\n')
            f.write(f'start "" "{exe_path}"\n')
            f.write('del "%~f0" /f /q\n')
            
        subprocess.Popen(bat_path, creationflags=subprocess.CREATE_NO_WINDOW)
        sys.exit(0)
    except Exception as e:
        messagebox.showerror("更新失败", f"下载或替换文件失败: {e}")
        sys.exit(0)

def self_destruct():
    exe_path = os.path.abspath(sys.argv[0])
    bat_path = os.path.join(os.environ['TEMP'], "seppuku.bat")
    
    with open(bat_path, "w", encoding='utf-8') as f:
        f.write('@echo off\n')
        f.write('ping 127.0.0.1 -n 3 > nul\n')
        if os.path.exists("config.json"):
            f.write('del "config.json" /f /q\n')
        if exe_path.endswith('.exe'):
            f.write(f'del "{exe_path}" /f /q\n')
        f.write('del "%~f0" /f /q\n') 
        
    subprocess.Popen(bat_path, creationflags=subprocess.CREATE_NO_WINDOW)
    sys.exit(0)

# ==========================================
# 软件主逻辑
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

class RedirectText(object):
    def __init__(self, text_widget):
        self.output = text_widget
    def write(self, string):
        self.output.insert(tk.END, string)
        self.output.see(tk.END)
    def flush(self): pass

class SmartJianYingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"剪映 AI 极速群导  v{APP_VERSION}")
        self.root.geometry("420x620")
        self.root.attributes('-topmost', True)
        self.root.resizable(False, False)
        
        # 右下角定位
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        self.root.geometry(f"+{sw - 440}+{sh - 670}")
        
        self.coords = {"export": None, "confirm": None, "popup_close": None, "draft_close": None}
        self.close_btn_color = None
        self.stop_flag = False
        self.recording_state = None
        self.api_key = DEFAULT_API_KEY
        self.rename_map = []
        self.drafts_list = []       # AI 识别出的草稿坐标列表
        self.start_from_index = 0   # 用户选择从哪一集开始
        
        self.load_config()
        self.setup_ui()
        self.show_cloud_warning()
        
        self.listener = keyboard.Listener(on_release=self.on_key_release)
        self.listener.start()

    def get_api_client(self):
        key = self.api_key_var.get().strip()
        if not key: key = DEFAULT_API_KEY
        return genai.Client(api_key=key)

    def show_cloud_warning(self):
        messagebox.showwarning("使用前必读",
            "⚠️ 重要安全提示 ⚠️\n\n"
            "所有需要【自动导出字幕】和【自动导出视频】的草稿，\n"
            "必须是从云空间刚下载到本地的，且绝对不能双击打开过！\n\n"
            "如果你提前打开了草稿，底层结构会被剪映修改，将导致提取失败或损坏！\n\n"
            "如果只为录制坐标，请单独建一个废弃草稿去录制。"
        )

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
            json.dump({
                "coords": self.coords,
                "color": self.close_btn_color,
                "api_key": self.api_key_var.get().strip()
            }, f)
        print("💾 配置已保存")

    # ========================== UI 搭建 ==========================
    def setup_ui(self):
        self.notebook = tb.Notebook(self.root, bootstyle="primary")
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        
        self.tab_export   = tb.Frame(self.notebook)
        self.tab_subtitle = tb.Frame(self.notebook)
        self.tab_rename   = tb.Frame(self.notebook)
        self.tab_settings = tb.Frame(self.notebook)
        
        self.notebook.add(self.tab_export,   text=" 导出 ")
        self.notebook.add(self.tab_subtitle, text=" 字幕 ")
        self.notebook.add(self.tab_rename,   text=" 重命名 ")
        self.notebook.add(self.tab_settings, text=" 设置 ")
        
        self.build_export_tab()
        self.build_subtitle_tab()
        self.build_rename_tab()
        self.build_settings_tab()
        
        # 底部状态栏
        self.status_var = tk.StringVar(value=f"v{APP_VERSION}  |  就绪")
        tb.Label(self.root, textvariable=self.status_var, foreground="gray",
                 font=("Consolas", 8)).pack(side="bottom", fill="x", padx=8, pady=2)

    # ---------- Tab 1: 自动导出 ----------
    def build_export_tab(self):
        # 坐标录制区 (紧凑)
        cf = tb.LabelFrame(self.tab_export, text=" 坐标录制 ", padding=6)
        cf.pack(fill="x", padx=8, pady=4)
        
        self.lbl_guide = tb.Label(cf, text="未录制", bootstyle="danger", wraplength=370, font=("", 9))
        self.lbl_guide.pack(fill="x")
        tb.Button(cf, text="🎯 引导录制 (F8)", bootstyle="info-outline",
                  command=self.start_wizard).pack(fill="x", pady=4)
        
        if self.coords.get('export'):
            self.lbl_guide.config(text="✅ 坐标就绪", bootstyle="success")

        # 任务列表
        tf = tb.LabelFrame(self.tab_export, text=" 任务列表 ", padding=6)
        tf.pack(fill="both", expand=True, padx=8, pady=4)
        
        cols = ("#", "坐标", "状态")
        self.task_tree = ttk.Treeview(tf, columns=cols, show="headings", height=6, selectmode="browse")
        self.task_tree.heading("#", text="#")
        self.task_tree.heading("坐标", text="坐标")
        self.task_tree.heading("状态", text="状态")
        self.task_tree.column("#", width=35, anchor="center")
        self.task_tree.column("坐标", width=140, anchor="center")
        self.task_tree.column("状态", width=80, anchor="center")
        self.task_tree.pack(fill="both", expand=True)
        
        tb.Button(tf, text="📸 AI 扫描首页草稿", bootstyle="secondary-outline",
                  command=self.scan_drafts_thread).pack(fill="x", pady=(4, 0))

        # 控制按钮
        bf = tb.Frame(self.tab_export)
        bf.pack(fill="x", padx=8, pady=6)
        
        self.start_btn = tb.Button(bf, text="▶ 从选中集开始", bootstyle="success",
                                   command=self.start_task)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 3), ipady=6)
        
        self.stop_btn = tb.Button(bf, text="⏹ 中断", bootstyle="danger",
                                  command=self.stop_task)
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=(3, 0), ipady=6)
        
        # 日志
        self.log_area = scrolledtext.ScrolledText(
            self.tab_export, height=6, bg="#1a1a2e", fg="#00ff9f",
            font=("Consolas", 8), insertbackground="#00ff9f", bd=0, relief="flat"
        )
        self.log_area.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        sys.stdout = RedirectText(self.log_area)

    # ---------- Tab 2: 字幕提取 ----------
    def build_subtitle_tab(self):
        pf = tb.LabelFrame(self.tab_subtitle, text=" 草稿目录 ", padding=8)
        pf.pack(fill="x", padx=8, pady=8)
        
        pi = tb.Frame(pf)
        pi.pack(fill="x")
        self.draft_path_var = tk.StringVar(value=r"D:\JianyingPro Drafts")
        tb.Entry(pi, textvariable=self.draft_path_var).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tb.Button(pi, text="...", width=3, command=self.browse_draft_path,
                  bootstyle="secondary-outline").pack(side="right")

        tb.Button(self.tab_subtitle, text="📥 提取字幕并删除轨道 → 桌面/srt", 
                  bootstyle="warning", command=self.extract_subtitles_thread
                  ).pack(fill="x", padx=8, pady=8, ipady=6)
        
        tb.Label(self.tab_subtitle,
                 text="• 提取 JSON 中的文本为 SRT 格式\n• 设置文本轨道 flag=2 (无字幕版)\n• 按集数命名保存到桌面 srt 文件夹", 
                 foreground="gray", justify="left", font=("", 9)
                 ).pack(padx=8, anchor="w")

    def browse_draft_path(self):
        folder = filedialog.askdirectory(initialdir=self.draft_path_var.get())
        if folder: self.draft_path_var.set(folder)

    # ---------- Tab 3: 智能重命名 ----------
    def build_rename_tab(self):
        ff = tb.LabelFrame(self.tab_rename, text=" 文件选择 ", padding=8)
        ff.pack(fill="x", padx=8, pady=4)
        
        self.selected_files = []
        tb.Button(ff, text="📁 选择视频文件", command=self.select_videos,
                  bootstyle="secondary-outline").pack(fill="x")
        self.lbl_file_count = tb.Label(ff, text="未选择", font=("", 9))
        self.lbl_file_count.pack(pady=2)
        
        pf = tb.LabelFrame(self.tab_rename, text=" 命名格式 ", padding=8)
        pf.pack(fill="x", padx=8, pady=4)
        self.rename_prompt = tb.Entry(pf)
        self.rename_prompt.insert(0, "短剧名称_第X集")
        self.rename_prompt.pack(fill="x")
        
        tb.Button(self.tab_rename, text="✨ AI 预览", bootstyle="info-outline",
                  command=self.preview_rename_thread).pack(fill="x", padx=8, pady=4)
        
        cols = ("原名", "新名")
        self.tree = ttk.Treeview(self.tab_rename, columns=cols, show="headings", height=5)
        self.tree.heading("原名", text="原文件名")
        self.tree.heading("新名", text="新文件名")
        self.tree.column("原名", width=170)
        self.tree.column("新名", width=170)
        self.tree.pack(fill="both", expand=True, padx=8, pady=2)
        
        self.btn_exec_rename = tb.Button(self.tab_rename, text="✅ 执行重命名",
                                         bootstyle="success", state="disabled",
                                         command=self.execute_rename)
        self.btn_exec_rename.pack(fill="x", padx=8, pady=6)

    def select_videos(self):
        files = filedialog.askopenfilenames(title="选择视频",
                    filetypes=[("视频", "*.mp4 *.mov *.avi *.mkv"), ("全部", "*.*")])
        if files:
            self.selected_files = list(files)
            self.lbl_file_count.config(text=f"已选 {len(self.selected_files)} 个文件")
            self.btn_exec_rename.config(state="disabled")
            for item in self.tree.get_children(): self.tree.delete(item)

    def preview_rename_thread(self):
        if not self.selected_files:
            messagebox.showwarning("提示", "请先选择文件")
            return
        threading.Thread(target=self.preview_rename_logic, daemon=True).start()

    def preview_rename_logic(self):
        print("🧠 AI 分析重命名...")
        file_names = [os.path.basename(f) for f in self.selected_files]
        prompt = (
            f"You are a file renaming assistant. Rename these video files.\n"
            f"Target format: {self.rename_prompt.get()}\n"
            f"Files:\n{json.dumps(file_names, ensure_ascii=False)}\n\n"
            f"Return ONLY a raw JSON array of objects with 'old' and 'new' keys. No markdown."
        )
        
        client = self.get_api_client()
        try:
            response = client.models.generate_content(
                model='gemini-3.6-flash', contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1)
            )
            text = response.text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            rename_data = json.loads(text.strip())
            self.root.after(0, self.update_rename_tree, rename_data)
        except Exception as e:
            print(f"❌ AI 重命名失败: {e}")

    def update_rename_tree(self, rename_data):
        for item in self.tree.get_children(): self.tree.delete(item)
        file_dir_map = {os.path.basename(f): os.path.dirname(f) for f in self.selected_files}
        self.rename_map = []
        for item in rename_data:
            old_name, new_name = item.get("old"), item.get("new")
            if old_name and new_name and old_name in file_dir_map:
                d = file_dir_map[old_name]
                self.rename_map.append((os.path.join(d, old_name), os.path.join(d, new_name)))
                self.tree.insert("", "end", values=(old_name, new_name))
        self.btn_exec_rename.config(state="normal")
        print("✅ 预览已生成")

    def execute_rename(self):
        if not self.rename_map: return
        ok = 0
        for old_path, new_path in self.rename_map:
            try: os.rename(old_path, new_path); ok += 1
            except Exception as e: print(f"❌ {os.path.basename(old_path)}: {e}")
        messagebox.showinfo("完成", f"✅ 成功重命名 {ok}/{len(self.rename_map)} 个文件")
        self.selected_files = []
        self.lbl_file_count.config(text="未选择")
        for item in self.tree.get_children(): self.tree.delete(item)
        self.btn_exec_rename.config(state="disabled")

    # ---------- Tab 4: 设置 ----------
    def build_settings_tab(self):
        af = tb.LabelFrame(self.tab_settings, text=" API Key ", padding=8)
        af.pack(fill="x", padx=8, pady=8)
        
        tb.Label(af, text="默认为作者公用 Key (有速率限制)\n建议前往 Google AI Studio 免费申请自己的 Key", 
                 foreground="orange", justify="left", font=("", 9)).pack(anchor="w", pady=4)
                 
        self.api_key_var = tk.StringVar(value=self.api_key)
        tb.Entry(af, textvariable=self.api_key_var, show="*").pack(fill="x", pady=4)
        tb.Button(af, text="保存", bootstyle="success-outline", command=self.save_settings).pack(anchor="e")

    def save_settings(self):
        self.save_config()
        messagebox.showinfo("成功", "设置已保存！")

    # ========================== 字幕提取 ==========================
    def extract_subtitles_thread(self):
        if not messagebox.askyesno("确认", "确认草稿全部来自云空间且未打开过？"): return
        threading.Thread(target=self.extract_subtitles_logic, daemon=True).start()

    def extract_subtitles_logic(self):
        print("\n🚀 提取字幕轨道...")
        base_dir = self.draft_path_var.get()
        if not os.path.exists(base_dir):
            print(f"❌ 目录不存在: {base_dir}"); return
            
        desktop_srt_dir = os.path.join(os.path.expanduser("~"), "Desktop", "srt")
        os.makedirs(desktop_srt_dir, exist_ok=True)
        
        drafts = []
        for fn in os.listdir(base_dir):
            fp = os.path.join(base_dir, fn)
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
            drafts.append({"jp": jp, "ep": ep, "fn": fn})
            
        drafts.sort(key=lambda x: x["ep"])
        
        counter, ok = 1, 0
        for d in drafts:
            try:
                with open(d["jp"], 'r', encoding='utf-8') as f:
                    data = json.load(f)
                texts_dict = {t['id']: t for t in data.get('materials', {}).get('texts', [])}
                text_tracks = [t for t in data.get('tracks', []) if t.get('type') == 'text']
                if not text_tracks: continue
                
                segs = []
                for track in text_tracks:
                    track['flag'] = 2
                    segs.extend(track.get('segments', []))
                segs.sort(key=lambda x: x.get('target_timerange', {}).get('start', 0))
                
                lines, idx = [], 1
                for seg in segs:
                    tm = texts_dict.get(seg.get('material_id'))
                    if not tm: continue
                    try: txt = json.loads(tm.get('content', '{}')).get('text', '')
                    except: txt = tm.get('content', '')
                    if not txt.strip(): continue
                    tr = seg.get('target_timerange', {})
                    s, dur = tr.get('start', 0), tr.get('duration', 0)
                    lines += [str(idx), f"{format_time(s)} --> {format_time(s+dur)}", txt.strip(), ""]
                    idx += 1
                
                if lines:
                    ep = d["ep"] if d["ep"] != 999999 else counter
                    sp = os.path.join(desktop_srt_dir, f"{ep}.srt")
                    if os.path.exists(sp): sp = os.path.join(desktop_srt_dir, f"{ep}_{counter}.srt")
                    with open(sp, 'w', encoding='utf-8') as f: f.write("\n".join(lines))
                    with open(d["jp"], 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
                    print(f"✅ {d['fn']} → {os.path.basename(sp)}")
                    ok += 1; counter += 1
            except Exception as e:
                print(f"❌ {d['fn']}: {e}")
        print(f"🎉 完成！提取 {ok} 个，保存在桌面 srt 文件夹")

    # ========================== 坐标向导 ==========================
    def start_wizard(self):
        self.recording_state = "export"
        self.lbl_guide.config(text="【1/4】鼠标放在右上角【导出】上，按 F8", bootstyle="info")
        print("\n🔧 向导开始：请按 F8 录制")

    def on_key_release(self, key):
        if key == keyboard.Key.f8 and self.recording_state:
            x, y = pyautogui.position()
            if self.recording_state == "export":
                self.coords["export"] = (x, y)
                self.recording_state = "confirm"
                self.root.after(0, lambda: self.lbl_guide.config(
                    text="【2/4】手动点导出，鼠标悬停蓝色【确认导出】上，按 F8"))
                print(f"✅ [导出] ({x}, {y})")
            elif self.recording_state == "confirm":
                self.coords["confirm"] = (x, y)
                self.recording_state = "popup_close"
                self.root.after(0, lambda: self.lbl_guide.config(
                    text="【3/4】手动点确认，等导出完成。鼠标悬停【关闭】上，按 F8"))
                print(f"✅ [确认导出] ({x}, {y})")
            elif self.recording_state == "popup_close":
                self.coords["popup_close"] = (x, y)
                r, g, b = pyautogui.pixel(x, y)
                self.close_btn_color = (r, g, b)
                self.recording_state = "draft_close"
                self.root.after(0, lambda: self.lbl_guide.config(
                    text="【4/4】点掉弹窗，鼠标悬停右上角【X 关闭草稿】上，按 F8"))
                print(f"✅ [弹窗关闭] ({x}, {y}) RGB:{r},{g},{b}")
            elif self.recording_state == "draft_close":
                self.coords["draft_close"] = (x, y)
                self.recording_state = None
                self.save_config()
                self.root.after(0, lambda: self.lbl_guide.config(text="✅ 录制完成！已保存", bootstyle="success"))
                print(f"✅ [X关闭] ({x}, {y})\n🎉 向导完成！")

    # ========================== AI 扫描与任务列表 ==========================
    def scan_drafts_thread(self):
        threading.Thread(target=self.scan_drafts_logic, daemon=True).start()

    def scan_drafts_logic(self):
        print("\n📸 截屏并呼叫 AI...")
        self.status_var.set("AI 扫描中...")
        screenshot_path = "home_screen.png"
        pyautogui.screenshot(screenshot_path)
        client = self.get_api_client()
        
        try:
            image = Image.open(screenshot_path)
            prompt = (
                "You are an AI analyzing a video editor's home screen. "
                "Find EVERY SINGLE video draft thumbnail (cover image) visible. "
                "Return their bounding boxes in exactly this format: [ymin, xmin, ymax, xmax] "
                "normalized to 1000. Provide nothing else."
            )
            response = client.models.generate_content(
                model='gemini-3.6-flash', contents=[image, prompt],
                config=types.GenerateContentConfig(temperature=0.1)
            )
            boxes = re.findall(r'\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]', response.text)
            centers = []
            sw, sh = pyautogui.size()
            for b in boxes:
                ymin, xmin, ymax, xmax = map(int, b)
                centers.append((int((xmin+xmax)/2/1000*sw), int((ymin+ymax)/2/1000*sh)))
                
            if not centers:
                print("⚠️ 没有识别到草稿")
                self.status_var.set("未识别到草稿")
                return
                
            # 排序：从上到下，从左到右
            centers.sort(key=lambda c: c[1])
            rows, cur = [], []
            for c in centers:
                if not cur: cur.append(c)
                else:
                    if abs(c[1] - cur[0][1]) < 100: cur.append(c)
                    else:
                        cur.sort(key=lambda i: i[0])
                        rows.append(cur)
                        cur = [c]
            if cur:
                cur.sort(key=lambda i: i[0])
                rows.append(cur)
            
            self.drafts_list = []
            for r in rows: self.drafts_list.extend(r)
            
            # 更新任务列表 UI
            self.root.after(0, self.populate_task_tree)
            print(f"🎯 识别到 {len(self.drafts_list)} 个草稿，将按从左到右、从上到下顺序导出")
            self.status_var.set(f"就绪  |  {len(self.drafts_list)} 个草稿待导出")
        except Exception as e:
            print(f"❌ AI 失败: {e}")
            self.status_var.set("扫描失败")

    def populate_task_tree(self):
        for item in self.task_tree.get_children(): self.task_tree.delete(item)
        for i, (x, y) in enumerate(self.drafts_list):
            self.task_tree.insert("", "end", iid=str(i), values=(f"第{i+1}集", f"({x}, {y})", "⏳ 待处理"))
        # 默认选中第一个
        if self.drafts_list:
            self.task_tree.selection_set("0")

    # ========================== 核心导出逻辑 ==========================
    def stop_task(self):
        self.stop_flag = True
        print("\n🛑 中止指令已接收")
        self.start_btn.config(state="normal")
        self.status_var.set("已中断")

    def wait_for_export_done(self):
        """
        两阶段像素监控法：
        1. 先等待像素颜色【不匹配】（确认进度条/对话框已出现覆盖了该区域）
        2. 再等待像素颜色【匹配】（确认导出完成，关闭按钮重新出现）
        这解决了第2集开始像素一直匹配导致直接跳过的bug。
        """
        x, y = self.coords['popup_close']
        tc = self.close_btn_color
        
        # 阶段 1：等待像素变为"不是目标颜色" (进度条出现)
        print("  └── ⏳ 等待导出进度条出现...")
        timeout = 0
        while not self.stop_flag:
            r, g, b = pyautogui.pixel(x, y)
            diff = abs(r-tc[0]) + abs(g-tc[1]) + abs(b-tc[2])
            if diff >= 30:
                break  # 像素变了，说明进度条/对话框已经覆盖了那个区域
            time.sleep(0.5)
            timeout += 1
            if timeout > 20:  # 10 秒超时保护
                break
                
        # 阶段 2：等待像素恢复为"目标颜色" (导出完成，关闭按钮出现)
        print("  └── ⏳ 导出进行中，等待完成...")
        while not self.stop_flag:
            r, g, b = pyautogui.pixel(x, y)
            diff = abs(r-tc[0]) + abs(g-tc[1]) + abs(b-tc[2])
            if diff < 30:
                print("  └── ✅ 导出完成！")
                break
            time.sleep(2)

    def start_task(self):
        if None in self.coords.values():
            messagebox.showerror("未就绪", "请先完成坐标录制向导！")
            return
        if not self.drafts_list:
            messagebox.showerror("未就绪", "请先点击【AI 扫描首页草稿】！")
            return
        
        # 获取用户选择的起始点
        sel = self.task_tree.selection()
        self.start_from_index = int(sel[0]) if sel else 0
            
        self.stop_flag = False
        self.start_btn.config(state="disabled")
        threading.Thread(target=self.run_logic, daemon=True).start()

    def check_stop(self):
        if self.stop_flag: raise Exception("用户主动中断")

    def update_task_status(self, idx, status):
        self.root.after(0, lambda: self.task_tree.set(str(idx), "状态", status))

    def run_logic(self):
        try:
            total = len(self.drafts_list)
            start = self.start_from_index
            print(f"\n{'='*30}")
            print(f"🚀 开始导出：从第 {start+1} 集到第 {total} 集")
            self.status_var.set(f"导出中  |  从第{start+1}集开始")
            
            for i in range(start, total):
                self.check_stop()
                dx, dy = self.drafts_list[i]
                self.update_task_status(i, "▶ 处理中")
                print(f"\n🎬 第{i+1}集...")
                
                # 1. 点击草稿
                pyautogui.click(dx, dy)
                print("  └── 🖱️ 点击草稿，加载 7s")
                for _ in range(7):
                    self.check_stop(); time.sleep(1)
                
                # 2. 点击导出
                self.check_stop()
                pyautogui.click(*self.coords['export'])
                print("  └── 🖱️ 点击[导出]")
                for _ in range(3):
                    self.check_stop(); time.sleep(1)
                
                # 3. 点击确认导出
                self.check_stop()
                pyautogui.click(*self.coords['confirm'])
                print("  └── 🖱️ 点击[确认导出]")
                
                # 4. 两阶段像素监控等待导出完成
                self.wait_for_export_done()
                self.check_stop()
                
                # 5. 点击关闭弹窗
                pyautogui.click(*self.coords['popup_close'])
                print("  └── 🖱️ 点击[关闭]")
                for _ in range(3):
                    self.check_stop(); time.sleep(1)
                
                # 6. 关闭草稿
                self.check_stop()
                pyautogui.click(*self.coords['draft_close'])
                print("  └── 🖱️ 点击[X]返回首页")
                
                self.update_task_status(i, "✅ 完成")
                
                print("  └── 💤 休息 5s")
                for _ in range(5):
                    self.check_stop(); time.sleep(1)
                    
                self.status_var.set(f"导出中  |  已完成 {i-start+1}/{total-start}")
                
            print("\n🎉 全部导出完毕！")
            self.status_var.set(f"全部完成  |  共 {total-start} 集")
            
        except Exception as e:
            print(f"\n⏹ {e}")
        finally:
            self.root.after(0, lambda: self.start_btn.config(state="normal"))

if __name__ == "__main__":
    check_authorization()
    app = tb.Window("剪映 AI 极速群导", themename="cosmo")
    
    style = tb.Style()
    style.configure('.', font=('Microsoft YaHei UI', 9))
    
    gui = SmartJianYingGUI(app)
    app.mainloop()
