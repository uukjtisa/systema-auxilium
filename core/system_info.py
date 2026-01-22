"""
System Information Detector
Detects system info at startup and formats it for AI prompt
"""

import platform
import os
import psutil


def get_system_info():
    """
    Get comprehensive system information

    Returns:
        dict: System information including OS, paths, specs
    """
    info = {
        'os': platform.system(),
        'os_release': platform.release(),
        'os_version': platform.version(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'python_version': platform.python_version(),
        'hostname': platform.node(),
        'username': os.getenv('USER') or os.getenv('USERNAME') or 'unknown',
        'home_dir': os.path.expanduser('~'),
        'current_dir': os.getcwd(),
        'cpu_count': psutil.cpu_count(logical=True),
        'cpu_count_physical': psutil.cpu_count(logical=False),
        'ram_total': psutil.virtual_memory().total,
        'ram_total_gb': round(psutil.virtual_memory().total / (1024 ** 3), 2),
    }

    # Get disk partitions
    partitions = []
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            partitions.append({
                'device': partition.device,
                'mountpoint': partition.mountpoint,
                'fstype': partition.fstype,
                'total_gb': round(usage.total / (1024 ** 3), 2),
                'free_gb': round(usage.free / (1024 ** 3), 2),
                'used_gb': round(usage.used / (1024 ** 3), 2),
            })
        except:
            pass

    info['disk_partitions'] = partitions

    # Detect common paths
    common_paths = {}
    if info['os'] == 'Windows':
        common_paths['desktop'] = os.path.join(info['home_dir'], 'Desktop')
        common_paths['documents'] = os.path.join(info['home_dir'], 'Documents')
        common_paths['downloads'] = os.path.join(info['home_dir'], 'Downloads')
        common_paths['pictures'] = os.path.join(info['home_dir'], 'Pictures')
        common_paths['music'] = os.path.join(info['home_dir'], 'Music')
        common_paths['videos'] = os.path.join(info['home_dir'], 'Videos')
        common_paths['appdata'] = os.path.join(info['home_dir'], 'AppData')
    else:
        common_paths['desktop'] = os.path.join(info['home_dir'], 'Desktop')
        common_paths['documents'] = os.path.join(info['home_dir'], 'Documents')
        common_paths['downloads'] = os.path.join(info['home_dir'], 'Downloads')

    info['common_paths'] = common_paths

    return info


def format_system_info_for_prompt(info):
    """
    Format system information as a readable string for AI prompt

    Args:
        info (dict): System information from get_system_info()

    Returns:
        str: Formatted system information
    """
    # Format disk partitions
    partitions_text = []
    for p in info['disk_partitions']:
        partitions_text.append(
            f"  - {p['mountpoint']}: {p['free_gb']} GB free / {p['total_gb']} GB total ({p['fstype']})"
        )

    # Format common paths
    paths_text = []
    for name, path in info['common_paths'].items():
        paths_text.append(f"  - {name.capitalize()}: {path}")

    formatted = f"""
=== SYSTEM INFORMATION ===

**Operating System:**
- OS: {info['os']} {info['os_release']}
- Machine: {info['machine']}
- Hostname: {info['hostname']}
- Username: {info['username']}

**Hardware:**
- CPU Cores: {info['cpu_count']} logical ({info['cpu_count_physical']} physical)
- RAM: {info['ram_total_gb']} GB total
- Processor: {info['processor']}

**Python Environment:**
- Python Version: {info['python_version']}
- Working Directory: {info['current_dir']}
- Home Directory: {info['home_dir']}

**Disk Partitions:**
{chr(10).join(partitions_text)}

**Common Paths:**
{chr(10).join(paths_text)}

=== END SYSTEM INFORMATION ===
"""

    return formatted.strip()


def get_app_launch_method(os_name):
    """
    Get the best method to launch applications for this OS

    Args:
        os_name (str): Operating system name from platform.system()

    Returns:
        str: Code example for launching apps
    """
    if os_name == 'Windows':
        return """# Windows: Use os.startfile() - it's the most reliable
import os

# For applications
os.startfile('cmd')  # Launches by name if in PATH
os.startfile(r'C:\\Program Files\\App\\App.exe')  # Full path (Search the Common Places for the Application)

# For files/folders
os.startfile('C:\\Users\\Username\\Desktop')  # Opens in Explorer
os.startfile('document.pdf')  # Opens with default app"""

    elif os_name == 'Linux':
        return """# Linux: Use subprocess with detached process
import subprocess

# Using xdg-open (works on most Linux distros)
subprocess.Popen(['xdg-open', 'spotify'])

# Or direct command
subprocess.Popen(['spotify'], 
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)"""

    elif os_name == 'Darwin':  # macOS
        return """# macOS: Use subprocess with 'open' command
import subprocess

# Using 'open' command
subprocess.Popen(['open', '-a', 'Spotify'])

# Or for files
subprocess.Popen(['open', '/path/to/file.pdf'])"""

    else:
        return """# Generic: Try subprocess
import subprocess
subprocess.Popen(['app-name'])"""