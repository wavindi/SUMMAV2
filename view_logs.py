#!/usr/bin/env python3
"""
Padel Scoreboard Log Viewer
View real-time and historical logs for sensor and backend services
ALSA warnings filtered
Service restart functionality with pigpiod reset for sensors
"""

import subprocess
import sys
import threading
import time
import argparse
from datetime import datetime, timedelta


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'
    GRAY = '\033[90m'
    MAGENTA = '\033[95m'


def print_header(text):
    """Print a formatted header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text:^80}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.END}\n")


def should_filter_line(line):
    """Check if a log line should be filtered out (filters ALSA warnings)."""
    filters = [
        "ALSA lib pcm.c",
        "ALSA audio warnings",
        "snd_pcm_recover",
        "ALSA underrun",
        "underrun occurred",
        "ALSA buffer underrun",
        "ALSA lib pcm",
        "Other ALSA lib warnings"
    ]
    
    line_lower = line.lower()
    for filter_pattern in filters:
        if filter_pattern.lower() in line_lower:
            return True
    return False


def colorize_log_line(line, service_name):
    """Add colors to log lines based on content."""
    if should_filter_line(line):
        return None
    
    line_lower = line.lower()
    
    if service_name == 'sensors':
        prefix = f"{Colors.GREEN}[SENSOR]{Colors.END} "
    else:
        prefix = f"{Colors.BLUE}[BACKEND]{Colors.END} "
    
    if any(word in line_lower for word in ['error', 'failed', 'exception', '❌', 'fatal']):
        return f"{prefix}{Colors.RED}{line}{Colors.END}"
    elif any(word in line_lower for word in ['warning', 'warn', '⚠️']):
        return f"{prefix}{Colors.YELLOW}{line}{Colors.END}"
    elif any(word in line_lower for word in ['success', 'completed', '✅', 'ready']):
        return f"{prefix}{Colors.GREEN}{line}{Colors.END}"
    elif any(word in line_lower for word in ['detection', 'hand', '👋']):
        return f"{prefix}{Colors.CYAN}{line}{Colors.END}"
    elif any(word in line_lower for word in ['swap', 'switch', '🔄']):
        return f"{prefix}{Colors.MAGENTA}{line}{Colors.END}"
    else:
        return f"{prefix}{line}"


def tail_service_logs(service_name, lines=50, follow=False, since=None):
    """Tail logs for a specific service."""
    cmd = ['journalctl', '-u', f'padel-{service_name}.service']
    
    if follow:
        cmd.append('-f')
    if lines:
        cmd.extend(['-n', str(lines)])
    if since:
        cmd.extend(['--since', since])
    
    try:
        if follow:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )
            
            print(f"{Colors.BOLD}📡 Streaming {service_name} logs (Ctrl+C to stop)...{Colors.END}")
            
            for line in process.stdout:
                if line.strip():
                    colored_line = colorize_log_line(line.strip(), service_name)
                    if colored_line:
                        print(colored_line)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        colored_line = colorize_log_line(line.strip(), service_name)
                        if colored_line:
                            print(colored_line)
            else:
                print(f"{Colors.RED}❌ Error reading logs: {result.stderr}{Colors.END}")
                
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}⏹️  Stopped streaming{Colors.END}")
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")


def tail_both_services(lines=50, follow=False, since=None):
    """Tail logs from both services simultaneously."""
    cmd_sensors = ['journalctl', '-u', 'padel-sensors.service']
    cmd_backend = ['journalctl', '-u', 'padel-backend.service']
    
    if follow:
        cmd_sensors.append('-f')
        cmd_backend.append('-f')
    if lines:
        cmd_sensors.extend(['-n', str(lines)])
        cmd_backend.extend(['-n', str(lines)])
    if since:
        cmd_sensors.extend(['--since', since])
        cmd_backend.extend(['--since', since])
    
    def stream_service(cmd, service_name):
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )
            for line in process.stdout:
                if line.strip():
                    colored_line = colorize_log_line(line.strip(), service_name)
                    if colored_line:
                        print(colored_line)
        except Exception as e:
            print(f"{Colors.RED}{service_name} error: {e}{Colors.END}")
    
    try:
        if follow:
            print(f"{Colors.BOLD}📡 Streaming BOTH services logs (Ctrl+C to stop)...{Colors.END}")
            
            sensor_thread = threading.Thread(target=stream_service, args=(cmd_sensors, 'sensors'), daemon=True)
            backend_thread = threading.Thread(target=stream_service, args=(cmd_backend, 'backend'), daemon=True)
            
            sensor_thread.start()
            backend_thread.start()
            
            while True:
                time.sleep(1)
        else:
            print_header("SENSOR LOGS")
            tail_service_logs('sensors', lines=lines, follow=False, since=since)
            print_header("BACKEND LOGS")
            tail_service_logs('backend', lines=lines, follow=False, since=since)
            
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}⏹️  Stopped streaming{Colors.END}")
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")


def show_service_status():
    """Show status of all services."""
    services = ['pigpiod', 'padel-sensors', 'padel-backend', 'padel-kiosk']
    
    print_header("SERVICE STATUS")
    
    for service in services:
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service],
                capture_output=True,
                text=True
            )
            status = result.stdout.strip()
            
            if status == 'active':
                status_icon = f"{Colors.GREEN}✅{Colors.END}"
            elif status == 'inactive':
                status_icon = f"{Colors.YELLOW}⏸️{Colors.END}"
            else:
                status_icon = f"{Colors.RED}❌{Colors.END}"
            
            print(f"{status_icon}  {service:25} {status}")
            
        except Exception as e:
            print(f"{Colors.RED}❌{Colors.END}  {service:25} error: {e}")
    print()


def restart_pigpiod():
    """Kill and restart pigpiod daemon."""
    print(f"{Colors.CYAN}📍 Step 1/3: Killing pigpiod processes...{Colors.END}")
    
    try:
        result = subprocess.run(
            ['sudo', 'killall', 'pigpiod'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            print(f"{Colors.GREEN}✅ pigpiod killed{Colors.END}")
        else:
            print(f"{Colors.GRAY}ℹ️  No pigpiod processes running{Colors.END}")
        
        time.sleep(1)
        
        print(f"{Colors.CYAN}📍 Step 2/3: Starting pigpiod daemon...{Colors.END}")
        result = subprocess.run(
            ['sudo', 'pigpiod'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            print(f"{Colors.GREEN}✅ pigpiod started{Colors.END}")
            time.sleep(1)
            return True
        else:
            print(f"{Colors.RED}❌ Failed to start pigpiod: {result.stderr}{Colors.END}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"{Colors.RED}❌ pigpiod command timed out{Colors.END}")
        return False
    except Exception as e:
        print(f"{Colors.RED}❌ Error managing pigpiod: {e}{Colors.END}")
        return False


def refresh_kiosk():
    """Refresh the kiosk browser page."""
    try:
        print(f"{Colors.CYAN}🔄 Refreshing kiosk browser...{Colors.END}")
        
        # Set DISPLAY and XAUTHORITY for the pi user
        env = {
            'DISPLAY': ':0',
            'XAUTHORITY': '/home/pi/.Xauthority'
        }
        
        # Method 1: Try xdotool to send F5 key
        result = subprocess.run(
            ['sudo', '-u', 'pi', 'DISPLAY=:0', 'xdotool', 'key', 'F5'],
            capture_output=True,
            text=True,
            timeout=3,
            env=env
        )
        
        if result.returncode == 0:
            print(f"{Colors.GREEN}✅ Kiosk page refreshed (F5){Colors.END}")
            return True
        
        # Method 2: Try wmctrl to focus and then refresh
        subprocess.run(
            ['sudo', '-u', 'pi', 'DISPLAY=:0', 'wmctrl', '-a', 'Chromium'],
            capture_output=True,
            timeout=2
        )
        time.sleep(0.5)
        subprocess.run(
            ['sudo', '-u', 'pi', 'DISPLAY=:0', 'xdotool', 'key', 'ctrl+r'],
            capture_output=True,
            timeout=2
        )
        
        print(f"{Colors.GREEN}✅ Kiosk page refreshed (Ctrl+R){Colors.END}")
        return True
        
    except subprocess.TimeoutExpired:
        print(f"{Colors.YELLOW}⚠️  Kiosk refresh timed out{Colors.END}")
        return False
    except Exception as e:
        print(f"{Colors.YELLOW}⚠️  Could not refresh kiosk: {e}{Colors.END}")
        return False


def restart_service(service_name):
    """Restart a specific service with force stop and cleanup."""
    print(f"\n{Colors.YELLOW}🔄 Restarting {service_name}...{Colors.END}\n")
    
    try:
        # Special handling for sensor service - reset pigpiod first
        if service_name == 'padel-sensors.service' or service_name == 'padel-sensors':
            service_name = 'padel-sensors.service'
            
            if not restart_pigpiod():
                print(f"{Colors.RED}❌ Failed to reset pigpiod, aborting sensor restart{Colors.END}")
                return False
            
            print(f"{Colors.CYAN}📍 Step 3/3: Restarting sensor service...{Colors.END}")
        
        # ✅ BACKEND SPECIAL HANDLING: Force stop and clean port
        elif service_name == 'padel-backend.service' or service_name == 'padel-backend':
            service_name = 'padel-backend.service'
            
            print(f"{Colors.CYAN}📍 Step 1/4: Force stopping backend...{Colors.END}")
            
            # Stop the service
            subprocess.run(
                ['sudo', 'systemctl', 'stop', service_name],
                capture_output=True,
                timeout=5
            )
            time.sleep(0.5)
            
            # Kill any stuck Python processes
            subprocess.run(
                ['sudo', 'pkill', '-9', '-f', 'padel_backend.py'],
                capture_output=True,
                timeout=5
            )
            
            # Kill anything using port 5000
            print(f"{Colors.CYAN}📍 Step 2/4: Cleaning port 5000...{Colors.END}")
            subprocess.run(
                ['sudo', 'fuser', '-k', '5000/tcp'],
                capture_output=True,
                timeout=5
            )
            
            time.sleep(1)
            print(f"{Colors.GREEN}✅ Backend stopped and port cleaned{Colors.END}")
            
            print(f"{Colors.CYAN}📍 Step 3/4: Starting backend...{Colors.END}")
        
        # Restart the service with timeout
        result = subprocess.run(
            ['sudo', 'systemctl', 'restart', service_name],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            time.sleep(2)  # Wait for service to start
            
            # Check if service is active
            status_result = subprocess.run(
                ['systemctl', 'is-active', service_name],
                capture_output=True,
                text=True
            )
            
            if status_result.stdout.strip() == 'active':
                print(f"{Colors.GREEN}✅ {service_name} restarted successfully!{Colors.END}")
                
                # ✅ REFRESH KIOSK IF BACKEND RESTARTED
                if 'backend' in service_name:
                    print(f"{Colors.CYAN}📍 Step 4/4: Refreshing kiosk display...{Colors.END}")
                    time.sleep(1)  # Give backend a moment to fully start
                    refresh_kiosk()
                
                return True
            else:
                print(f"{Colors.RED}❌ {service_name} failed to start{Colors.END}")
                return False
        else:
            print(f"{Colors.RED}❌ Error restarting {service_name}: {result.stderr}{Colors.END}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"{Colors.RED}❌ Restart command timed out! Trying force restart...{Colors.END}")
        
        # Force kill and restart
        subprocess.run(['sudo', 'systemctl', 'kill', service_name], capture_output=True)
        time.sleep(1)
        subprocess.run(['sudo', 'systemctl', 'restart', service_name], capture_output=True)
        
        time.sleep(2)
        status_result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True,
            text=True
        )
        
        if status_result.stdout.strip() == 'active':
            print(f"{Colors.GREEN}✅ {service_name} force restarted{Colors.END}")
            if 'backend' in service_name:
                refresh_kiosk()
            return True
        else:
            print(f"{Colors.RED}❌ Force restart failed{Colors.END}")
            return False
            
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")
        return False


def restart_all_services():
    """Restart all padel services."""
    services = [
        ('padel-sensors.service', 'Sensor Service'),
        ('padel-backend.service', 'Backend Service'),
        ('padel-kiosk.service', 'Kiosk Service')
    ]
    
    print_header("RESTARTING ALL SERVICES")
    
    success_count = 0
    for service, display_name in services:
        print(f"\n{Colors.BOLD}{display_name}{Colors.END}")
        if restart_service(service):
            success_count += 1
        time.sleep(0.5)
    
    print()
    if success_count == len(services):
        print(f"{Colors.GREEN}✅ All {len(services)} services restarted successfully!{Colors.END}")
    else:
        print(f"{Colors.YELLOW}⚠️  {success_count}/{len(services)} services restarted{Colors.END}")


def search_logs(service_name, pattern, lines=100):
    """Search for pattern in logs."""
    cmd = ['journalctl', '-u', f'padel-{service_name}.service', '-n', str(lines)]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            matching_lines = []
            for line in result.stdout.split('\n'):
                if pattern.lower() in line.lower():
                    colored_line = colorize_log_line(line.strip(), service_name)
                    if colored_line:
                        matching_lines.append(colored_line)
            
            if matching_lines:
                print(f"{Colors.GREEN}✅ Found {len(matching_lines)} matching lines:{Colors.END}\n")
                for line in matching_lines:
                    print(line)
            else:
                print(f"{Colors.YELLOW}⚠️  No matches found for '{pattern}'{Colors.END}")
        else:
            print(f"{Colors.RED}❌ Error searching logs: {result.stderr}{Colors.END}")
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")


def interactive_menu():
    """Show interactive menu."""
    while True:
        print_header("PADEL SCOREBOARD LOG VIEWER")
        
        print(f"{Colors.BOLD}Real-time Logs:{Colors.END}")
        print("  1. Stream sensor logs (live)")
        print("  2. Stream backend logs (live)")
        print("  3. Stream BOTH services (live)")
        print()
        
        print(f"{Colors.BOLD}Historical Logs:{Colors.END}")
        print("  4. View last 50 sensor logs")
        print("  5. View last 50 backend logs")
        print("  6. View last 50 logs from BOTH")
        print("  7. View last 200 sensor logs")
        print("  8. View last 200 backend logs")
        print()
        
        print(f"{Colors.BOLD}Time-based:{Colors.END}")
        print("  9. View logs from last hour")
        print("  10. View logs from today")
        print()
        
        print(f"{Colors.BOLD}Service Management:{Colors.END}")
        print(f"  {Colors.GREEN}11. Restart Sensor Service{Colors.END} (with pigpiod reset)")
        print(f"  {Colors.GREEN}12. Restart Backend Service{Colors.END} (force stop + refresh kiosk)")
        print(f"  {Colors.GREEN}13. Restart Kiosk Service{Colors.END}")
        print(f"  {Colors.YELLOW}14. Restart ALL Services{Colors.END}")
        print(f"  {Colors.CYAN}15. Service Status{Colors.END}")
        print()
        
        print(f"{Colors.BOLD}Other:{Colors.END}")
        print("  16. Search logs")
        print()
        print("  0. Exit")
        print()
        
        try:
            choice = input(f"{Colors.CYAN}Enter choice: {Colors.END}").strip()
            print()
            
            if choice == '1':
                tail_service_logs('sensors', lines=20, follow=True)
            elif choice == '2':
                tail_service_logs('backend', lines=20, follow=True)
            elif choice == '3':
                tail_both_services(lines=20, follow=True)
            elif choice == '4':
                tail_service_logs('sensors', lines=50, follow=False)
            elif choice == '5':
                tail_service_logs('backend', lines=50, follow=False)
            elif choice == '6':
                tail_both_services(lines=50, follow=False)
            elif choice == '7':
                tail_service_logs('sensors', lines=200, follow=False)
            elif choice == '8':
                tail_service_logs('backend', lines=200, follow=False)
            elif choice == '9':
                tail_both_services(lines=500, follow=False, since='1 hour ago')
            elif choice == '10':
                tail_both_services(lines=1000, follow=False, since='today')
            elif choice == '11':
                restart_service('padel-sensors.service')
            elif choice == '12':
                restart_service('padel-backend.service')
            elif choice == '13':
                restart_service('padel-kiosk.service')
            elif choice == '14':
                confirm = input(f"{Colors.YELLOW}⚠️  Restart ALL services? (yes/no): {Colors.END}").strip().lower()
                if confirm in ['yes', 'y']:
                    restart_all_services()
                else:
                    print(f"{Colors.GRAY}Cancelled{Colors.END}")
            elif choice == '15':
                show_service_status()
            elif choice == '16':
                service = input("Service (sensors/backend): ").strip()
                pattern = input("Search pattern: ").strip()
                if service in ['sensors', 'backend'] and pattern:
                    search_logs(service, pattern, lines=500)
                else:
                    print(f"{Colors.RED}❌ Invalid input{Colors.END}")
            elif choice == '0':
                print(f"{Colors.GREEN}👋 Goodbye!{Colors.END}")
                break
            else:
                print(f"{Colors.RED}❌ Invalid choice{Colors.END}")
            
            if choice != '0':
                input(f"\n{Colors.GRAY}Press Enter to continue...{Colors.END}")
                print("\n" * 2)
                
        except KeyboardInterrupt:
            print(f"\n{Colors.GREEN}👋 Goodbye!{Colors.END}")
            break
        except Exception as e:
            print(f"{Colors.RED}❌ Error: {e}{Colors.END}")
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(
        description='Padel Scoreboard Log Viewer with ALSA filtering and service management',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                        # Interactive menu
  %(prog)s -s sensors -f          # Stream sensor logs
  %(prog)s -s backend -n 100      # Show last 100 backend logs
  %(prog)s -b -f                  # Stream both services
  %(prog)s -s sensors --search error  # Search for "error" in sensor logs
  %(prog)s --status               # Show service status
  %(prog)s --restart sensors      # Restart sensor service (with pigpiod reset)
  %(prog)s --restart backend      # Restart backend (force stop + refresh kiosk)
  %(prog)s --restart all          # Restart all services
  %(prog)s --since "1 hour ago"   # Logs from last hour
        '''
    )
    
    parser.add_argument('-s', '--service', choices=['sensors', 'backend'], 
                       help='Service to view (sensors or backend)')
    parser.add_argument('-b', '--both', action='store_true',
                       help='View both services')
    parser.add_argument('-f', '--follow', action='store_true',
                       help='Stream logs in real-time')
    parser.add_argument('-n', '--lines', type=int, default=50,
                       help='Number of lines to show (default: 50)')
    parser.add_argument('--since', type=str,
                       help='Show logs since time (e.g., "1 hour ago", "today")')
    parser.add_argument('--search', type=str,
                       help='Search for pattern in logs')
    parser.add_argument('--status', action='store_true',
                       help='Show service status')
    parser.add_argument('--restart', type=str, choices=['sensors', 'backend', 'kiosk', 'all'],
                       help='Restart services - sensors includes pigpiod reset, backend force stops and refreshes kiosk')
    
    args = parser.parse_args()
    
    # If no arguments, show interactive menu
    if len(sys.argv) == 1:
        interactive_menu()
        return
    
    # Show status
    if args.status:
        show_service_status()
        return
    
    # Restart services
    if args.restart:
        if args.restart == 'all':
            restart_all_services()
        else:
            restart_service(f'padel-{args.restart}.service')
        return
    
    # Search logs
    if args.search and args.service:
        search_logs(args.service, args.search, args.lines)
        return
    
    # View logs
    if args.both:
        tail_both_services(lines=args.lines, follow=args.follow, since=args.since)
    elif args.service:
        tail_service_logs(args.service, lines=args.lines, follow=args.follow, since=args.since)
    else:
        parser.print_help()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.GREEN}👋 Goodbye!{Colors.END}")
        sys.exit(0)
    except Exception as e:
        print(f"{Colors.RED}❌ Fatal error: {e}{Colors.END}")
        sys.exit(1)
