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
APP_VERSION = "1.0.1"

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
            
        # 检查自动更新
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
    """ 
    利用 Windows bat 脚本在后台延迟执行删除命令，实现程序运行中自毁 
    """
    exe_path = os.path.abspath(sys.argv[0])
    bat_path = os.path.join(os.environ['TEMP'], "seppuku.bat")
    
    # 构建自毁命令 (删除 exe 本身，删除所有相关 json 配置，最后删除 bat 自己)
    with open(bat_path, "w", encoding='utf-8') as f:
        f.write('@echo off\n')
        f.write('ping 127.0.0.1 -n 3 > nul\n') # 等待3秒让 python 进程彻底退出
        
        # 删除所有相关配置文件
        if os.path.exists("config.json"):
            f.write('del "config.json" /f /q\n')
        if os.path.exists("coords.json"):
            f.write('del "coords.json" /f /q\n')
            
        # 如果当前是打包后的 exe，删除 exe
        if exe_path.endswith('.exe'):
            f.write(f'del "{exe_path}" /f /q\n')
            
        # 删除脚本自身
        f.write('del "%~f0" /f /q\n') 
        
    # 在后台无窗口静默执行这个 bat
    subprocess.Popen(bat_path, creationflags=subprocess.CREATE_NO_WINDOW)
    sys.exit(0) # 立即退出当前程序

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
        self.root.title("剪映 AI 生产力工具箱 - 专业版")
        self.root.geometry("650x750")
        self.root.attributes('-topmost', True)
        
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        self.root.geometry(f"+{screen_width - 670}+{screen_height - 800}")
        
        self.coords = {
            "export": None,
            "confirm": None,
            "popup_close": None,
            "draft_close": None
        }
        self.close_btn_color = None
        self.stop_flag = False
        self.recording_state = None
        self.api_key = DEFAULT_API_KEY
        self.rename_map = [] 
        
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
        warning_msg = (
            "⚠️ 重要安全提示 ⚠️\n\n"
            "所有需要【自动导出字幕】和【自动导出视频】的草稿，\n"
            "必须是从云空间刚下载到本地的，且绝对不能双击打开过！\n\n"
            "如果你提前打开了草稿，底层结构会被剪映修改，将导致提取失败或损坏！\n\n"
            "如果只为录制坐标，请单独建一个废弃草稿去录制。"
        )
        messagebox.showwarning("使用前必读", warning_msg)

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
        print("💾 配置数据已永久保存。")

    def setup_ui(self):
        self.notebook = tb.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.tab_export = tb.Frame(self.notebook)
        self.tab_subtitle = tb.Frame(self.notebook)
        self.tab_rename = tb.Frame(self.notebook)
        self.tab_settings = tb.Frame(self.notebook)
        
        self.notebook.add(self.tab_export, text="🚀 自动导出")
        self.notebook.add(self.tab_subtitle, text="📝 字幕提取")
        self.notebook.add(self.tab_rename, text="✨ 智能重命名")
        self.notebook.add(self.tab_settings, text="⚙️ 系统设置")
        
        self.build_export_tab()
        self.build_subtitle_tab()
        self.build_rename_tab()
        self.build_settings_tab()

    def build_export_tab(self):
        coord_frame = tb.LabelFrame(self.tab_export, text=" 坐标初始化 (新用户或分辨率改变时必点) ", padding=10)
        coord_frame.pack(fill="x", padx=10, pady=10)
        
        self.lbl_guide = tb.Label(coord_frame, text="尚未录制坐标，请点击下方按钮开始向导。", bootstyle="danger", wraplength=450)
        self.lbl_guide.pack(fill="x", pady=5)
        
        tb.Button(coord_frame, text="🎯 引导录制坐标 (模拟完整走一遍导出流程)", 
                  bootstyle="info", command=self.start_wizard).pack(fill="x", pady=5)
        
        if self.coords.get('export'):
            self.lbl_guide.config(text="✅ 坐标已全部就绪！可以直接点击【开始本地全自动运行】。", bootstyle="success")

        ctrl_frame = tb.Frame(self.tab_export)
        ctrl_frame.pack(fill="x", padx=10, pady=10)
        
        self.start_btn = tb.Button(ctrl_frame, text="▶ 开始本地全自动运行", bootstyle="success", command=self.start_task)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 5), ipady=8)
        
        self.stop_btn = tb.Button(ctrl_frame, text="⏹ 紧急中断", bootstyle="danger", command=self.stop_task)
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=(5, 0), ipady=8)
        
        self.log_area = scrolledtext.ScrolledText(self.tab_export, width=40, height=12, bg="#1E1E1E", fg="#00FF00", font=("Consolas", 8))
        self.log_area.pack(fill="both", expand=True, padx=10, pady=10)
        sys.stdout = RedirectText(self.log_area)

    def build_subtitle_tab(self):
        path_frame = tb.LabelFrame(self.tab_subtitle, text=" 草稿目录配置 ", padding=10)
        path_frame.pack(fill="x", padx=10, pady=10)
        
        tb.Label(path_frame, text="剪映草稿路径:").pack(anchor="w")
        
        path_inner = tb.Frame(path_frame)
        path_inner.pack(fill="x", pady=5)
        self.draft_path_var = tk.StringVar(value=r"D:\JianyingPro Drafts")
        tb.Entry(path_inner, textvariable=self.draft_path_var).pack(side="left", fill="x", expand=True, padx=(0, 5))
        tb.Button(path_inner, text="浏览", command=self.browse_draft_path, bootstyle="secondary-outline").pack(side="right")

        tb.Button(self.tab_subtitle, text="📥 提取并删除草稿字幕轨道 -> 保存到桌面srt文件夹", 
                  bootstyle="warning", command=self.extract_subtitles_thread).pack(fill="x", padx=10, pady=10, ipady=8)
        
        tb.Label(self.tab_subtitle, text="此操作将：\n1. 提取JSON中的文本为SRT格式\n2. 禁用JSON中的文本轨道(无字幕版)\n3. 按照集数保存在桌面的 srt 文件夹内", 
                 foreground="gray", justify="left").pack(padx=10, pady=10, anchor="w")

    def browse_draft_path(self):
        folder = filedialog.askdirectory(initialdir=self.draft_path_var.get())
        if folder: self.draft_path_var.set(folder)

    def build_rename_tab(self):
        file_frame = tb.LabelFrame(self.tab_rename, text=" 视频文件选择 ", padding=10)
        file_frame.pack(fill="x", padx=10, pady=5)
        
        self.selected_files = []
        tb.Button(file_frame, text="📁 点击选择需要重命名的视频文件", command=self.select_videos).pack(fill="x", pady=5)
        self.lbl_file_count = tb.Label(file_frame, text="未选择文件")
        self.lbl_file_count.pack()
        
        prompt_frame = tb.LabelFrame(self.tab_rename, text=" 命名格式要求 ", padding=10)
        prompt_frame.pack(fill="x", padx=10, pady=5)
        self.rename_prompt = tb.Entry(prompt_frame)
        self.rename_prompt.insert(0, "短剧名称_第X集")
        self.rename_prompt.pack(fill="x", pady=5)
        
        tb.Button(self.tab_rename, text="✨ AI 预览重命名", bootstyle="info", command=self.preview_rename_thread).pack(fill="x", padx=10, pady=5)
        
        cols = ("Old", "New")
        self.tree = ttk.Treeview(self.tab_rename, columns=cols, show="headings", height=8)
        self.tree.heading("Old", text="原文件名")
        self.tree.heading("New", text="新文件名")
        self.tree.column("Old", width=200)
        self.tree.column("New", width=200)
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.btn_exec_rename = tb.Button(self.tab_rename, text="✅ 确认执行重命名", bootstyle="success", state="disabled", command=self.execute_rename)
        self.btn_exec_rename.pack(fill="x", padx=10, pady=10)

    def select_videos(self):
        files = filedialog.askopenfilenames(title="选择视频", filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv"), ("All files", "*.*")])
        if files:
            self.selected_files = list(files)
            self.lbl_file_count.config(text=f"已选择 {len(self.selected_files)} 个文件")
            self.btn_exec_rename.config(state="disabled")
            for item in self.tree.get_children():
                self.tree.delete(item)

    def preview_rename_thread(self):
        if not self.selected_files:
            messagebox.showwarning("警告", "请先选择需要重命名的文件！")
            return
        threading.Thread(target=self.preview_rename_logic, daemon=True).start()

    def preview_rename_logic(self):
        print("🧠 正在呼叫 AI 分析重命名规则...")
        file_names = [os.path.basename(f) for f in self.selected_files]
        prompt = (
            f"You are a file renaming assistant. The user wants to rename a batch of video files.\n"
            f"Target format rule: {self.rename_prompt.get()}\n"
            f"Original files:\n{json.dumps(file_names, ensure_ascii=False)}\n\n"
            f"Return ONLY a valid JSON array of objects. Each object must have an 'old' key with the original filename, and a 'new' key with the new filename. "
            f"Do not use markdown formatting like ```json, just return the raw JSON array."
        )
        
        client = self.get_api_client()
        try:
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1)
            )
            
            text = response.text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            
            rename_data = json.loads(text.strip())
            
            self.rename_map = []
            self.root.after(0, self.update_rename_tree, rename_data)
        except Exception as e:
            print(f"❌ AI 重命名失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("AI 错误", f"分析失败: {e}"))

    def update_rename_tree(self, rename_data):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        file_dir_map = {os.path.basename(f): os.path.dirname(f) for f in self.selected_files}
        
        self.rename_map = []
        for item in rename_data:
            old_name = item.get("old")
            new_name = item.get("new")
            if old_name and new_name and old_name in file_dir_map:
                directory = file_dir_map[old_name]
                old_path = os.path.join(directory, old_name)
                new_path = os.path.join(directory, new_name)
                self.rename_map.append((old_path, new_path))
                self.tree.insert("", "end", values=(old_name, new_name))
                
        self.btn_exec_rename.config(state="normal")
        print("✅ AI 预览已生成，请在界面确认！")

    def execute_rename(self):
        if not self.rename_map: return
        success = 0
        for old_path, new_path in self.rename_map:
            try:
                os.rename(old_path, new_path)
                success += 1
            except Exception as e:
                print(f"❌ 重命名失败: {os.path.basename(old_path)} -> {e}")
                
        messagebox.showinfo("重命名完成", f"✅ 原文件名称已修改。\n成功重命名 {success}/{len(self.rename_map)} 个文件！")
        self.selected_files = []
        self.lbl_file_count.config(text="未选择文件")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.btn_exec_rename.config(state="disabled")

    def build_settings_tab(self):
        api_frame = tb.LabelFrame(self.tab_settings, text=" AI API 密钥设置 ", padding=10)
        api_frame.pack(fill="x", padx=10, pady=10)
        
        tb.Label(api_frame, text="注意：默认使用的是作者提供的公用 API Key。\n由于调用量大，可能会遇到速率限制 (503错误)。\n强烈鼓励你去 Google AI Studio 免费申请自己的 Key 并填入下方！", 
                 foreground="red", justify="left").pack(pady=5, anchor="w")
                 
        self.api_key_var = tk.StringVar(value=self.api_key)
        tb.Entry(api_frame, textvariable=self.api_key_var, width=50, show="*").pack(pady=10, fill="x")
        
        tb.Button(api_frame, text="保存设置", bootstyle="success", command=self.save_settings).pack(pady=5)

    def save_settings(self):
        self.save_config()
        messagebox.showinfo("成功", "设置已保存！")

    def extract_subtitles_thread(self):
        resp = messagebox.askyesno("最后确认", "再次确认：你要处理的草稿是否全是从云空间刚下载且【没有打开过】的？\n\n是的话点击Yes开始提取，否则请点No取消。")
        if not resp: return
        threading.Thread(target=self.extract_subtitles_logic, daemon=True).start()

    def extract_subtitles_logic(self):
        print("\n=================================")
        print("🚀 开始提取并删除字幕轨道...")
        base_dir = self.draft_path_var.get()
        if not os.path.exists(base_dir):
            print(f"❌ 找不到草稿目录: {base_dir}")
            return
            
        desktop_srt_dir = os.path.join(os.path.expanduser("~"), "Desktop", "srt")
        os.makedirs(desktop_srt_dir, exist_ok=True)
        
        drafts_list = []
        for folder_name in os.listdir(base_dir):
            folder_path = os.path.join(base_dir, folder_name)
            if not os.path.isdir(folder_path) or folder_name == 'srt': continue
            
            info_path = os.path.join(folder_path, 'draft_info.json')
            json_path = os.path.join(folder_path, 'draft_content.json')
            if not os.path.exists(json_path): continue
            
            ep_num = None
            if os.path.exists(info_path):
                try:
                    with open(info_path, 'r', encoding='utf-8') as f:
                        info_data = json.load(f)
                        draft_name = info_data.get('draft_name', '')
                        match = re.search(r'\d+', draft_name)
                        if match: ep_num = int(match.group())
                except: pass
                
            if ep_num is None:
                match = re.search(r'\d+', folder_name)
                if match: ep_num = int(match.group())
                else: ep_num = 999999
                    
            drafts_list.append({
                "folder_path": folder_path, "json_path": json_path,
                "ep_num": ep_num, "folder_name": folder_name
            })
            
        drafts_list.sort(key=lambda x: x["ep_num"])
        
        counter = 1
        success_count = 0
        for draft in drafts_list:
            try:
                with open(draft["json_path"], 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                texts_dict = {t['id']: t for t in data.get('materials', {}).get('texts', [])}
                text_tracks = [t for t in data.get('tracks', []) if t.get('type') == 'text']
                
                if not text_tracks: continue
                
                all_segments = []
                for track in text_tracks:
                    track['flag'] = 2 
                    for seg in track.get('segments', []):
                        all_segments.append(seg)
                        
                all_segments = sorted(all_segments, key=lambda x: x.get('target_timerange', {}).get('start', 0))
                
                srt_lines = []
                srt_index = 1
                for seg in all_segments:
                    material_id = seg.get('material_id')
                    timerange = seg.get('target_timerange', {})
                    start_us = timerange.get('start', 0)
                    end_us = start_us + timerange.get('duration', 0)
                    
                    text_material = texts_dict.get(material_id)
                    if not text_material: continue
                        
                    content_str = text_material.get('content', '{}')
                    try:
                        content_json = json.loads(content_str)
                        text_content = content_json.get('text', content_str)
                    except:
                        text_content = content_str
                        
                    if not text_content.strip(): continue
                        
                    srt_lines.append(f"{srt_index}")
                    srt_lines.append(f"{format_time(start_us)} --> {format_time(end_us)}")
                    srt_lines.append(text_content.strip())
                    srt_lines.append("")
                    srt_index += 1
                
                if srt_lines:
                    ep = draft["ep_num"] if draft["ep_num"] != 999999 else counter
                    srt_path = os.path.join(desktop_srt_dir, f"{ep}.srt")
                    if os.path.exists(srt_path):
                        srt_path = os.path.join(desktop_srt_dir, f"{ep}_{counter}.srt")
                        
                    with open(srt_path, 'w', encoding='utf-8') as f:
                        f.write("\n".join(srt_lines))
                    
                    with open(draft["json_path"], 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False)
                        
                    print(f"✅ 提取成功: {draft['folder_name']} -> {os.path.basename(srt_path)}")
                    success_count += 1
                    counter += 1
                    
            except Exception as e:
                print(f"❌ 解析出错 {draft['folder_name']}: {e}")
                
        print(f"🎉 全部完成！共提取 {success_count} 个草稿。SRT保存在桌面 srt 文件夹。")

    def start_wizard(self):
        self.recording_state = "export"
        self.lbl_guide.config(text="【第 1 步】打开废弃草稿，把鼠标放在右上角的【导出】上，按【F8】键", bootstyle="info")
        print("\n🔧 向导：请按 F8 录制【导出】按钮。")

    def on_key_release(self, key):
        if key == keyboard.Key.f8 and self.recording_state:
            x, y = pyautogui.position()
            if self.recording_state == "export":
                self.coords["export"] = (x, y)
                self.recording_state = "confirm"
                self.root.after(0, lambda: self.lbl_guide.config(text="【第 2 步】手动点开导出，把鼠标悬停在蓝色的【确认导出】上，按【F8】键"))
                print(f"✅ 记录 [导出] 坐标: {x}, {y}")
            elif self.recording_state == "confirm":
                self.coords["confirm"] = (x, y)
                self.recording_state = "popup_close"
                self.root.after(0, lambda: self.lbl_guide.config(text="【第 3 步】手动点确认导出，等进度条走完。把鼠标悬停在【关闭弹窗】按钮上，按【F8】（会吸取颜色）"))
                print(f"✅ 记录 [确认导出] 坐标: {x}, {y}")
            elif self.recording_state == "popup_close":
                self.coords["popup_close"] = (x, y)
                r, g, b = pyautogui.pixel(x, y)
                self.close_btn_color = (r, g, b)
                self.recording_state = "draft_close"
                self.root.after(0, lambda: self.lbl_guide.config(text="【第 4 步】点掉弹窗，把鼠标悬停在最右上角关闭草稿的【X】上，按【F8】键"))
                print(f"✅ 记录 [弹窗关闭] 坐标与颜色: {x}, {y} RGB:{r},{g},{b}")
            elif self.recording_state == "draft_close":
                self.coords["draft_close"] = (x, y)
                self.recording_state = None
                self.save_config()
                self.root.after(0, lambda: self.lbl_guide.config(text="✅ 坐标录制完成！已永久保存。", bootstyle="success"))
                print(f"✅ 记录 [草稿右上角X] 坐标: {x}, {y}\n🎉 初始化向导全部完成！")

    def stop_task(self):
        self.stop_flag = True
        print("\n🛑 已接收到中止指令，当前动作完成后将安全退出...")
        self.start_btn.config(state="normal")

    def get_draft_order_with_ai(self):
        print("\n📸 正在截取首页屏幕，呼叫 AI 识图...")
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
                centers.append((int((xmin + xmax) / 2 / 1000 * sw), int((ymin + ymax) / 2 / 1000 * sh)))
                
            if not centers: return []
            centers.sort(key=lambda c: c[1]) 
            rows, current_row = [], []
            for c in centers:
                if not current_row: current_row.append(c)
                else:
                    if abs(c[1] - current_row[0][1]) < 100: current_row.append(c)
                    else:
                        current_row.sort(key=lambda i: i[0])
                        rows.append(current_row)
                        current_row = [c]
            if current_row:
                current_row.sort(key=lambda i: i[0])
                rows.append(current_row)
                
            ordered_drafts = []
            for r in rows: ordered_drafts.extend(r)
            return ordered_drafts
        except Exception as e:
            print(f"❌ AI 解析失败: {e}")
            return []

    def wait_for_pixel_color(self, target_coords, target_color):
        x, y = target_coords
        while not self.stop_flag:
            r, g, b = pyautogui.pixel(x, y)
            if abs(r - target_color[0]) + abs(g - target_color[1]) + abs(b - target_color[2]) < 30:
                print("  └── ✅ 检测到导出完成弹窗！")
                break
            time.sleep(2)

    def start_task(self):
        if None in self.coords.values():
            messagebox.showerror("未就绪", "请先点击【引导录制坐标】走一遍向导！")
            return
        self.stop_flag = False
        self.start_btn.config(state="disabled")
        threading.Thread(target=self.run_logic, daemon=True).start()

    def check_stop(self):
        if self.stop_flag: raise Exception("用户主动中断")

    def run_logic(self):
        try:
            print("======================")
            drafts = self.get_draft_order_with_ai()
            if self.stop_flag: return
            
            print(f"🎯 AI 成功识别到 {len(drafts)} 个视频草稿！")
            if not drafts: return
            
            for i, (dx, dy) in enumerate(drafts):
                self.check_stop()
                print(f"\n🚀 开始处理第 {i+1} 个视频...")
                pyautogui.click(dx, dy)
                print("  └── 🖱️ 点击草稿，等待 7 秒加载...")
                for _ in range(7):
                    self.check_stop(); time.sleep(1)
                
                self.check_stop()
                pyautogui.click(*self.coords['export'])
                print("  └── 🖱️ 点击右上角[导出]，间隔 3 秒")
                for _ in range(3):
                    self.check_stop(); time.sleep(1)
                
                self.check_stop()
                pyautogui.click(*self.coords['confirm'])
                print("  └── 🖱️ 点击[确认导出]")
                
                print("  └── 💻 本地导出：正在监控像素，等待导出完成...")
                self.wait_for_pixel_color(self.coords['popup_close'], self.close_btn_color)
                self.check_stop()
                
                pyautogui.click(*self.coords['popup_close'])
                print("  └── 🖱️ 点击弹窗[关闭]，间隔 3 秒")
                for _ in range(3):
                    self.check_stop(); time.sleep(1)
                
                self.check_stop()
                pyautogui.click(*self.coords['draft_close'])
                print("  └── 🖱️ 点击右上角[X]关闭草稿返回首页")
                
                print("  └── 💤 循环结束，休息 5 秒...")
                for _ in range(5):
                    self.check_stop(); time.sleep(1)
                
            print("\n🎉 全部视频处理完毕！")
            
        except Exception as e:
            print(f"\n⏹ 任务结束: {e}")
        finally:
            self.root.after(0, lambda: self.start_btn.config(state="normal"))

if __name__ == "__main__":
    check_authorization()
    app = tb.Window("剪映 AI 生产力工具箱", themename="litera")
    
    # 全局字体稍微放大，看起来更大气
    style = tb.Style()
    style.configure('.', font=('Helvetica', 10))
    
    gui = SmartJianYingGUI(app)
    app.mainloop()
