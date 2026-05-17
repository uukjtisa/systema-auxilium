"""
core/system_info.py
System Information Detector
Detects system info at startup and formats it for AI prompt
"""

import platform
import os
from core.logger import _make_logger, _NoOpLogger
import psutil
import requests
from datetime import datetime


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = False
log = _make_logger("SystemInfo") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


def _get_location_info() -> dict:
    """
    Attempt to get approximate location from IP geolocation.
    Returns a dict with location data or an error message.
    NOTE: This is IP-based and can be inaccurate or spoofed by VPN/proxy.
    """
    try:
        r = requests.get("http://ip-api.com/json/", timeout=5)
        if r.status_code == 200:
            d = r.json()
            return {
                "city":        d.get("city", "Unknown"),
                "region":      d.get("regionName", "Unknown"),
                "country":     d.get("country", "Unknown"),
                "country_code": d.get("countryCode", "??"),
                "zip":         d.get("zip", ""),
                "latitude":    d.get("lat", 0.0),
                "longitude":   d.get("lon", 0.0),
                "timezone":    d.get("timezone", "Unknown"),
                "isp":         d.get("isp", "Unknown"),
                "ip":          d.get("query", "Unknown"),
                "status":      "ok",
            }
    except Exception as e:
        pass
    return {"status": "unavailable", "reason": "Could not reach IP geolocation service."}

def get_system_info():
    """
    Get comprehensive system information

    Returns:
        dict: System information including OS, paths, specs
    """
    log.info("[get_system_info] ── Starting system information collection ──────────────")

    # ── OS / Identity ──────────────────────────────────────────────────────────
    log.debug("[get_system_info] Collecting OS and identity info via platform module")
    info = {
        'os':             platform.system(),
        'os_release':     platform.release(),
        'os_version':     platform.version(),
        'machine':        platform.machine(),
        'processor':      platform.processor(),
        'python_version': platform.python_version(),
        'hostname':       platform.node(),
        'username':       os.getenv('USER') or os.getenv('USERNAME') or 'unknown',
        'home_dir':       os.path.expanduser('~'),
        'current_dir':    os.getcwd(),
    }
    log.info(f"[get_system_info] OS: {info['os']} {info['os_release']} | "
             f"machine: {info['machine']} | user: {info['username']} | "
             f"python: {info['python_version']}")
    log.debug(f"[get_system_info] hostname='{info['hostname']}' | "
              f"home='{info['home_dir']}' | cwd='{info['current_dir']}'")

    # ── Hardware / CPU ─────────────────────────────────────────────────────────
    log.debug("[get_system_info] Querying CPU and RAM via psutil")
    info['cpu_count']          = psutil.cpu_count(logical=True)
    info['cpu_count_physical'] = psutil.cpu_count(logical=False)
    info['ram_total']          = psutil.virtual_memory().total
    info['ram_total_gb']       = round(psutil.virtual_memory().total / (1024 ** 3), 2)
    log.info(f"[get_system_info] CPU: {info['cpu_count']} logical / "
             f"{info['cpu_count_physical']} physical | RAM: {info['ram_total_gb']} GB")

    # ── Disk Partitions ────────────────────────────────────────────────────────
    log.debug("[get_system_info] Enumerating disk partitions")
    partitions = []
    for partition in psutil.disk_partitions():
        log.debug(f"[get_system_info] Inspecting partition: device='{partition.device}' | "
                  f"mountpoint='{partition.mountpoint}' | fstype='{partition.fstype}'")
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            entry = {
                'device':     partition.device,
                'mountpoint': partition.mountpoint,
                'fstype':     partition.fstype,
                'total_gb':   round(usage.total / (1024 ** 3), 2),
                'free_gb':    round(usage.free  / (1024 ** 3), 2),
                'used_gb':    round(usage.used  / (1024 ** 3), 2),
            }
            partitions.append(entry)
            log.info(f"[get_system_info] Partition '{partition.mountpoint}': "
                     f"{entry['used_gb']} GB used / {entry['total_gb']} GB total "
                     f"({entry['free_gb']} GB free) [{entry['fstype']}]")
        except Exception as e:
            log.warning(f"[get_system_info] Could not read usage for '{partition.mountpoint}': "
                        f"{type(e).__name__}: {e} — skipping")

    info['disk_partitions'] = partitions
    log.debug(f"[get_system_info] {len(partitions)} readable partition(s) collected")

    # ── Common Paths ───────────────────────────────────────────────────────────
    log.debug(f"[get_system_info] Detecting common paths for OS: '{info['os']}'")
    common_paths = {}
    if info['os'] == 'Windows':
        common_paths['desktop']   = os.path.join(info['home_dir'], 'Desktop')
        common_paths['documents'] = os.path.join(info['home_dir'], 'Documents')
        common_paths['downloads'] = os.path.join(info['home_dir'], 'Downloads')
        common_paths['pictures']  = os.path.join(info['home_dir'], 'Pictures')
        common_paths['music']     = os.path.join(info['home_dir'], 'Music')
        common_paths['videos']    = os.path.join(info['home_dir'], 'Videos')
        common_paths['appdata']   = os.path.join(info['home_dir'], 'AppData')
        log.debug("[get_system_info] Windows paths set (7 entries incl. AppData)")
    else:
        common_paths['desktop']   = os.path.join(info['home_dir'], 'Desktop')
        common_paths['documents'] = os.path.join(info['home_dir'], 'Documents')
        common_paths['downloads'] = os.path.join(info['home_dir'], 'Downloads')
        log.debug("[get_system_info] Non-Windows paths set (3 entries)")

    for path_name, path_val in common_paths.items():
        exists = os.path.exists(path_val)
        log.debug(f"[get_system_info] Path '{path_name}': '{path_val}' | exists={exists}")

    info['common_paths'] = common_paths

    # ── Date / Time ────────────────────────────────────────────────────────────
    log.debug("[get_system_info] Recording current date and time")
    info['current_datetime'] = datetime.now().strftime("%A, %B %d, %Y – %I:%M %p")
    log.info(f"[get_system_info] Current datetime: {info['current_datetime']}")

    # ── IP-based Location ─────────────────────────────────────────────────────
    log.debug("[get_system_info] Fetching IP-based location info")
    info['location'] = _get_location_info()
    if info['location'].get('status') == 'ok':
        log.info(f"[get_system_info] Location: {info['location']['city']}, "
                 f"{info['location']['region']}, {info['location']['country']}")
    else:
        log.warning("[get_system_info] Location unavailable")

    log.info(f"[get_system_info] ── Collection complete — {len(info)} top-level keys gathered ──")
    return info


def format_system_info_for_prompt(info):
    """
    Format system information as a readable string for AI prompt

    Args:
        info (dict): System information from get_system_info()

    Returns:
        str: Formatted system information
    """
    log.info("[format_system_info_for_prompt] Formatting system info dict into prompt string")
    log.debug(f"[format_system_info_for_prompt] Input keys: {list(info.keys())}")

    # Format disk partitions
    log.debug(f"[format_system_info_for_prompt] Formatting {len(info['disk_partitions'])} partition(s)")
    partitions_text = []
    for p in info['disk_partitions']:
        partitions_text.append(
            f"  - {p['mountpoint']}: {p['free_gb']} GB free / {p['total_gb']} GB total ({p['fstype']})"
        )

    # Format common paths
    log.debug(f"[format_system_info_for_prompt] Formatting {len(info['common_paths'])} common path(s)")
    paths_text = []
    for name, path in info['common_paths'].items():
        paths_text.append(f"  - {name.capitalize()}: {path}")

        # Format location
        loc = info.get('location', {})
        if loc.get('status') == 'ok':
            location_text = (
                f"  - City: {loc['city']}, {loc['region']}, {loc['country']} ({loc['country_code']})\n"
                f"  - Coordinates: {loc['latitude']}, {loc['longitude']}\n"
                f"  - Timezone: {loc['timezone']}\n"
                f"  - IP Address: {loc['ip']}  |  ISP: {loc['isp']}"
            )
        else:
            location_text = "  - Location data unavailable."

        formatted = f"""
    === SYSTEM INFORMATION AS OF SYSTEM INITIALIZATION ===

    Operating System:
    - OS: {info['os']} {info['os_release']}
    - Machine: {info['machine']}
    - Hostname: {info['hostname']}
    - Username: {info['username']}

    Hardware:
    - CPU Cores: {info['cpu_count']} logical ({info['cpu_count_physical']} physical)
    - RAM: {info['ram_total_gb']} GB total
    - Processor: {info['processor']}

    Python Environment:
    - Python Version: {info['python_version']}
    - Working Directory: {info['current_dir']}
    - Home Directory: {info['home_dir']}

    Disk Partitions:
    {chr(10).join(partitions_text)}

    Common Paths:
    {chr(10).join(paths_text)}

    Date & Time of System Initialization:
    - {info.get('current_datetime', 'Unavailable')}

    Active Location (IP-based):
    {location_text}
    ⚠ NOTE: The location above is derived from the user's IP address and may be imprecise or
       intentionally set via VPN/proxy. Treat it as the "active location" the user has set.
       If the user asks about their location, refer to the values above.
       Do not claim certainty — acknowledge it reflects the detected network location.
       
       More over, these values are the values at system initialization, so never treat this as
       the current state of the system.

    === END SYSTEM INFORMATION ===
    """

    log.info(f"[format_system_info_for_prompt] ✓ Formatted — output length: {len(formatted)} chars")
    return formatted.strip()


def get_app_launch_method(os_name):
    """
    Get the best method to launch applications for this OS

    Args:
        os_name (str): Operating system name from platform.system()

    Returns:
        str: Code example for launching apps
    """
    log.info(f"[get_app_launch_method] Determining launch method for OS: '{os_name}'")

    if os_name == 'Windows':
        log.debug("[get_app_launch_method] Returning Windows os.startfile() snippet")
        return """# Windows: Use os.startfile() - it's the most reliable
import os

# For applications
os.startfile('cmd')  # Launches by name if in PATH
os.startfile(r'C:\\Program Files\\App\\App.exe')  # Full path (Search the Common Places for the Application)

# For files/folders
os.startfile('C:\\Users\\Username\\Desktop')  # Opens in Explorer
os.startfile('document.pdf')  # Opens with default app"""

    elif os_name == 'Linux':
        log.debug("[get_app_launch_method] Returning Linux subprocess/xdg-open snippet")
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
        log.debug("[get_app_launch_method] Returning macOS 'open' command snippet")
        return """# macOS: Use subprocess with 'open' command
import subprocess

# Using 'open' command
subprocess.Popen(['open', '-a', 'Spotify'])

# Or for files
subprocess.Popen(['open', '/path/to/file.pdf'])"""

    else:
        log.warning(f"[get_app_launch_method] Unrecognized OS '{os_name}' — returning generic snippet")
        return """# Generic: Try subprocess
import subprocess
subprocess.Popen(['app-name'])"""
