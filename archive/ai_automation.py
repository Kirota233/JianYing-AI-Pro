import pyautogui
import time
import os
import psutil
import json
import re
from PIL import Image
from google import genai
from google.genai import types

class JianYingAIAutomation:
    def __init__(self, api_key, config_path="config.json"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
            
        self.action_delay = 7.0 
        self.client = genai.Client(api_key=api_key)
        self.screen_width, self.screen_height = pyautogui.size()
        
    def check_condition_with_ai(self, question):
        """让 AI 检查屏幕上是否满足某个条件，遇到 503 等网络错误会无限重试"""
        print(f"\n🔍 [AI 检查]: {question}")
        screenshot_path = "current_screen.png"
        pyautogui.screenshot(screenshot_path)
        
        attempt = 0
        while True:
            attempt += 1
            try:
                image = Image.open(screenshot_path)
                prompt = (
                    f"You are a precise UI automation agent. Look at this screenshot. "
                    f"Question: {question} "
                    f"Answer strictly with 'YES' or 'NO'. Do not include any other text."
                )
                
                response = self.client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=[image, prompt],
                    config=types.GenerateContentConfig(temperature=0.1)
                )
                
                result_text = response.text.strip().upper()
                print(f"  └── 💡 [AI 答复]: {result_text}")
                return 'YES' in result_text
                
            except Exception as e:
                error_str = str(e)
                print(f"  └── ⚠️ [网络/API异常 (第 {attempt} 次尝试)]: {error_str[:100]}...")
                print(f"  └── ↻ 等待 5 秒后继续重试...")
                time.sleep(5)

    def find_and_click_with_ai(self, target_description):
        """让 AI 寻找屏幕坐标并点击，遇到 503 等网络错误会无限重试"""
        print(f"\n🎯 [AI 定位]: 正在寻找【{target_description}】...")
        screenshot_path = "current_screen.png"
        pyautogui.screenshot(screenshot_path)
        
        attempt = 0
        while True:
            attempt += 1
            try:
                image = Image.open(screenshot_path)
                prompt = (
                    f"You are a precise UI automation agent. Look at this screenshot of a video editing software. "
                    f"Find the exact location of the: {target_description}. "
                    f"You MUST return the bounding box coordinates in the exact format [ymin, xmin, ymax, xmax] "
                    f"normalized between 0 and 1000. Do not include any other text."
                )
                
                response = self.client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=[image, prompt],
                    config=types.GenerateContentConfig(temperature=0.1)
                )
                
                result_text = response.text.strip()
                
                match = re.search(r'\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]', result_text)
                if match:
                    ymin, xmin, ymax, xmax = map(int, match.groups())
                    center_x_norm = (xmin + xmax) / 2
                    center_y_norm = (ymin + ymax) / 2
                    
                    pixel_x = int((center_x_norm / 1000) * self.screen_width)
                    pixel_y = int((center_y_norm / 1000) * self.screen_height)
                    
                    print(f"  └── ✅ [成功找到]: 屏幕坐标 ({pixel_x}, {pixel_y})")
                    print(f"  └── 🖱️ [执行点击]: 移动鼠标并点击该位置...")
                    
                    pyautogui.moveTo(pixel_x, pixel_y, duration=0.5)
                    pyautogui.click()
                    
                    print(f"  └── ⏳ [等待操作生效]: 休眠 {self.action_delay} 秒")
                    time.sleep(self.action_delay)
                    return True
                else:
                    # AI 没按格式返回坐标，可能没找到
                    print(f"  └── ❓ [AI 未找到目标]: AI 的原话是 '{result_text[:50]}'")
                    return False
                    
            except Exception as e:
                error_str = str(e)
                print(f"  └── ⚠️ [网络/API异常 (第 {attempt} 次尝试)]: {error_str[:100]}...")
                print(f"  └── ↻ 等待 5 秒后继续重试...")
                time.sleep(5)
            
    def export_current_draft(self):
        # 1. 检查是否有“正在处理的音频”
        is_processing = self.check_condition_with_ai("画面中是否有'正在处理音频'或类似正在加载/处理的进度提示？(Is there any text or popup saying 'processing audio' or similar progress?)")
        if is_processing:
            print("  └── 🕒 [检测到音频处理中]: 额外等待 7 秒...")
            time.sleep(7.0)
        else:
            print("  └── ⏩ [未检测到音频处理]: 正在无缝进行下一步...")
            
        # 2. 点击右上角的导出按钮
        if not self.find_and_click_with_ai("右上角的 '导出' 按钮 (The 'Export' button in the top right corner)"):
            print("❌ 无法找到 '导出' 按钮。")
            return False
            
        # 3. 弹窗里的蓝色导出确认按钮
        if not self.find_and_click_with_ai("弹窗界面底部巨大的蓝色 '导出' 确认按钮 (The large blue 'Confirm Export' button at the bottom of the dialog)"):
            print("❌ 无法找到 '确认导出' 按钮。")
            return False
            
        # 4. 监控进度，直到出现关闭按钮
        print("\n⏳ [进度监控]: 导出任务已开始，正在静默等待完成...")
        while True:
            if self.find_and_click_with_ai("导出完成界面上的 '关闭' 按钮 (The 'Close' button that appears after export is 100% complete)"):
                print("🎉 [导出成功]: 已点击关闭对话框！")
                break
            time.sleep(self.action_delay)
            
        # 5. 关闭草稿退回首页
        self.find_and_click_with_ai("界面最左上角的 '×' 按钮，用来关闭当前草稿 (The 'X' close button in the top left corner to exit the editor)")
        return True
