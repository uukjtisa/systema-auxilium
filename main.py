"""
Systema Auxilium - Operating System Helper Agent
An AI assistant that can control the system via Python interpreter

Original Architecture & Implementation:
    - Nicanor III W. Cariasa
"""

import sys
import ctypes
from PyQt6.QtWidgets import QApplication
from core.controller import AssistantController

def hide_console_window():
    """Hide the console window on Windows if launched from CMD"""
    if sys.platform == 'win32':
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                return True
        except:
            pass
    return False

def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    app.setApplicationName("Systema Auxilium - AI System Agent")
    app.setOrganizationName("NicProjects")

    # CRITICAL: Check if LLaMA is the selected provider with a model
    from pathlib import Path
    import json

    settings_file = "assistant_settings.json"
    if Path(settings_file).exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)

            if settings.get('ai_provider') == 'llama':
                print("\n" + "=" * 70)
                print("⚠️  LLaMA PROVIDER DETECTED")
                print("=" * 70)
                print("LLaMA is set as your AI provider.")
                print("Checking for model...")

                # Check if model exists
                models_dir = Path("llama_models")
                if models_dir.exists():
                    models = list(models_dir.glob("*.gguf"))
                    if models:
                        print(f"✓ Model found: {models[0].name}")
                        print("\n🕐 LOADING WILL TAKE TIME (30-120 seconds)")
                        print("   Please be patient while the model loads into RAM...")
                        print("=" * 70 + "\n")
                    else:
                        print("⚠️  NO MODEL FOUND!")
                        print("   Place a .gguf model in llama_models/ folder")
                        print("=" * 70 + "\n")
                else:
                    print("⚠️  llama_models/ folder not found")
                    print("=" * 70 + "\n")
        except:
            pass

    # Initialize controller
    controller = AssistantController()
    controller.show()

    print("======================CAUTION======================\n\nThis AI can execute system-level actions if ran with sufficient permissions.\nUse caution when issuing prompts.\nYou are responsible for the actions taken.\n\n======================CAUTION======================")

    # Hide CMD window after initialization (if launched from CMD)
    console_hidden = hide_console_window()
    if console_hidden:
        print("[Startup] Console window hidden (toggle in Debug Window)")

    sys.exit(app.exec())

if __name__ == "__main__":
    main()