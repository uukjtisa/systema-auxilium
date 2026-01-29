"""
Systema Auxilium - Operating System Helper Agent
An AI assistant that can control the system via Python interpreter

Original Architecture & Implementation:
    - Niccc2007
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
    print(
        "======================CAUTION======================\n\nThis AI can execute system-level actions if ran with sufficient permissions.\nUse caution when issuing prompts.\nYou are responsible for the actions taken.\n\n======================CAUTION======================")
    # Initialize controller
    controller = AssistantController()
    controller.show()

    # Hide CMD window after initialization (if launched from CMD)
    console_hidden = hide_console_window()
    if console_hidden:
        print("[Startup] Console window hidden (toggle in Debug Window)")

    sys.exit(app.exec())

if __name__ == "__main__":
    main()