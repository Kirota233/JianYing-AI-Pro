import pyautogui
import cv2
import time
import os
import psutil
import json

class JianYingAutomation:
    def __init__(self, config_path="config.json"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
            
        self.assets_dir = os.path.join(os.path.dirname(__file__), 'assets')
        self.delays = self.config['delays']
        
    def find_and_click(self, image_name, confidence=None):
        if confidence is None:
            confidence = self.config['confidence_threshold']
            
        img_path = os.path.join(self.assets_dir, image_name)
        if not os.path.exists(img_path):
            print(f"[Error] Asset not found: {img_path}")
            return False
            
        try:
            location = pyautogui.locateCenterOnScreen(img_path, confidence=confidence)
            if location:
                pyautogui.click(location)
                print(f"[Success] Clicked {image_name}")
                time.sleep(self.delays['ui_action'])
                return True
            else:
                print(f"[Warning] Could not find {image_name} on screen.")
                return False
        except Exception as e:
            print(f"[Exception] find_and_click for {image_name} failed: {e}")
            return False

    def is_process_running(self, process_name="JianyingPro.exe"):
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] == process_name:
                return True
        return False
        
    def launch_jianying(self):
        if not self.is_process_running():
            print("Launching JianYing...")
            os.startfile(self.config['jianying_path'])
            time.sleep(self.delays['launch'])
        else:
            print("JianYing is already running.")
            
    def export_current_draft(self):
        # 1. Click the 'Export' button in the main editor UI
        if not self.find_and_click('export_button.png'):
            print("Cannot find 'Export' button. Is the draft opened?")
            return False
            
        # 2. Wait for the export settings window to appear and click 'Export' again
        time.sleep(2)
        if not self.find_and_click('confirm_export_button.png'):
            print("Cannot find 'Confirm Export' button in the dialog.")
            return False
            
        # 3. Monitor export progress. We look for 'Close' or 'Cancel' or 'Complete' button
        print("Export started, waiting for completion...")
        while True:
            # If the "close_project.png" or "export_done_close.png" appears, it means done
            if pyautogui.locateCenterOnScreen(os.path.join(self.assets_dir, 'export_done_close.png'), confidence=0.8):
                print("Export completed!")
                self.find_and_click('export_done_close.png')
                break
            time.sleep(self.delays['export_check_interval'])
            
        # 4. Close the draft and return to home
        self.find_and_click('close_draft_button.png')
        time.sleep(3) # Wait for home screen
        return True
