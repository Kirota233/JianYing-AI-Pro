import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import sys
import time
from ai_automation import JianYingAIAutomation

class RedirectText(object):
    def __init__(self, text_widget):
        self.output = text_widget

    def write(self, string):
        self.output.insert(tk.END, string)
        self.output.see(tk.END)

    def flush(self):
        pass

class JianYingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("剪映 AI 视觉全自动批量导出工具")
        self.root.geometry("600x500")
        
        # API Key 区域
        tk.Label(root, text="Google Gemini API Key:").pack(pady=(10,0), anchor="w", padx=20)
        self.api_key_entry = tk.Entry(root, width=60, show="*")
        self.api_key_entry.insert(0, "AIzaSyDQ4s-9ynGQcJw6oNDF5G2fNewnuF1zkaY") # 默认填入
        self.api_key_entry.pack(pady=5, padx=20, fill="x")
        
        # 导出数量区域
        frame_settings = tk.Frame(root)
        frame_settings.pack(pady=10, padx=20, fill="x")
        
        tk.Label(frame_settings, text="需要导出的草稿数量:").pack(side="left")
        self.count_var = tk.IntVar(value=5)
        tk.Spinbox(frame_settings, from_=1, to=100, textvariable=self.count_var, width=10).pack(side="left", padx=10)
        
        # 启动按钮
        self.start_btn = tk.Button(root, text="▶ 开始批量导出 (Start)", bg="#4CAF50", fg="white", font=("Arial", 12, "bold"), command=self.start_automation)
        self.start_btn.pack(pady=15, padx=20, fill="x", ipady=5)
        
        # 日志输出区域
        tk.Label(root, text="运行日志:").pack(anchor="w", padx=20)
        self.log_area = scrolledtext.ScrolledText(root, width=70, height=15, bg="black", fg="#00FF00", font=("Consolas", 9))
        self.log_area.pack(pady=5, padx=20, fill="both", expand=True)
        
        # 重定向 stdout 到日志框
        sys.stdout = RedirectText(self.log_area)
        
    def start_automation(self):
        api_key = self.api_key_entry.get().strip()
        count = self.count_var.get()
        
        if not api_key:
            messagebox.showerror("错误", "请输入 API Key！")
            return
            
        self.start_btn.config(state="disabled", text="⏳ 运行中... (请勿移动鼠标)")
        
        # 在新线程中运行自动化，避免卡死 GUI
        threading.Thread(target=self.run_automation_task, args=(api_key, count), daemon=True).start()
        
    def run_automation_task(self, api_key, export_count):
        try:
            print("===========================================")
            print("🚀 === 初始化 AI 视觉自动化引擎 ===")
            bot = JianYingAIAutomation(api_key=api_key)
            
            print("⚠️ 【警告】程序将在 5 秒后接管鼠标，请确保：")
            print("   1. 剪映已【最大化】")
            print("   2. 停留在草稿列表首页")
            print("   3. 双手离开鼠标键盘！")
            print("-------------------------------------------")
            for i in range(5, 0, -1):
                print(f"⏳ 倒计时 {i} 秒...")
                time.sleep(1)
                
            for i in range(export_count):
                print(f"\n===========================================")
                print(f"🎬 开始处理视频草稿 (第 {i+1} / {export_count} 个)")
                print(f"===========================================")
                
                # 点击第一个草稿
                if not bot.find_and_click_with_ai("剪映首页草稿列表中的 '第一个视频草稿' 的缩略图封面 (The thumbnail of the very first video draft in the home screen list)"):
                    print("❌ [致命错误] 找不到草稿，请确保处于剪映首页！")
                    break
                    
                print("⏳ 等待 7 秒让草稿充分加载...")
                time.sleep(7.0)
                
                success = bot.export_current_draft()
                if not success:
                    print("❌ [致命错误] 当前草稿导出失败，批量任务中止。")
                    break
                    
            print("\n🎉 ========================================= 🎉")
            print("       批量导出任务全部完成！恭喜！")
            print("🎉 ========================================= 🎉")
            
        except Exception as e:
            print(f"\n[程序异常退出]: {e}")
            
        finally:
            # 恢复按钮状态
            self.root.after(0, lambda: self.start_btn.config(state="normal", text="▶ 开始批量导出 (Start)"))

if __name__ == "__main__":
    root = tk.Tk()
    app = JianYingGUI(root)
    root.mainloop()
