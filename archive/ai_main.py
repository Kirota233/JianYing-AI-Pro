import time
import sys
import os
from ai_automation import JianYingAIAutomation

GEMINI_API_KEY = "AIzaSyDQ4s-9ynGQcJw6oNDF5G2fNewnuF1zkaY"

def main():
    print("=== JianYing AI-Vision Batch Exporter ===")
        
    bot = JianYingAIAutomation(api_key=GEMINI_API_KEY)
    
    print("Starting AI automation in 3 seconds. DO NOT MOVE YOUR MOUSE.")
    time.sleep(3)
    
    try:
        bot.launch_jianying()
    except Exception as e:
        print(f"Could not auto-launch JianYing: {e}")
        print("Please manually open JianYing to the Home Screen (where your drafts are listed).")
        print("Waiting 10 seconds for you to open it...")
        time.sleep(10)
    
    export_count = bot.config['export_count']
    
    for i in range(export_count):
        print(f"\n--- Processing Draft {i+1} of {export_count} ---")
        
        if not bot.find_and_click_with_ai("剪映首页草稿列表中的 '第一个视频草稿' 的缩略图封面 (The thumbnail of the very first video draft in the home screen list)"):
            print("Could not find the draft to open. Make sure JianYing home screen is visible.")
            break
            
        print("Waiting for draft to load...")
        time.sleep(15) # Wait a bit longer for draft to fully load
        
        success = bot.export_current_draft()
        if not success:
            print("Export failed for this draft. Aborting batch process.")
            break
            
    print("\n=== AI Batch Export Completed ===")

if __name__ == "__main__":
    main()
