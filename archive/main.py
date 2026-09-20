import time
import sys
from automation import JianYingAutomation

def main():
    print("=== JianYing Batch Exporter ===")
    bot = JianYingAutomation()
    
    # Check if assets exist
    import os
    required_assets = ['export_button.png', 'confirm_export_button.png', 'export_done_close.png', 'close_draft_button.png', 'first_draft.png']
    missing_assets = [a for a in required_assets if not os.path.exists(os.path.join(bot.assets_dir, a))]
    
    if missing_assets:
        print("\n[ERROR] Missing the following screenshot assets in 'assets' folder:")
        for a in missing_assets:
            print(f" - {a}")
        print("\nPlease capture these UI elements and save them in the 'assets' directory before running.")
        sys.exit(1)

    print("Assets check passed. Starting automation in 3 seconds...")
    time.sleep(3)
    
    bot.launch_jianying()
    
    export_count = bot.config['export_count']
    
    for i in range(export_count):
        print(f"\n--- Processing Draft {i+1} of {export_count} ---")
        
        # Click the first draft in the list
        if not bot.find_and_click('first_draft.png'):
            print("Could not find the draft to open. Make sure JianYing home screen is visible.")
            break
            
        # Wait for the draft to load
        print("Waiting for draft to load...")
        time.sleep(8)
        
        success = bot.export_current_draft()
        if not success:
            print("Export failed for this draft. Aborting batch process.")
            break
            
    print("\n=== Batch Export Completed ===")

if __name__ == "__main__":
    main()
