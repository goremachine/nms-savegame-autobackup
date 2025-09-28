# auto_backup.py

import os
import json
import time
import sys
import shutil
import logging
import zipfile
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import queue
import threading
from datetime import datetime
import subprocess
from watchdog.observers import Observer
if sys.platform == "win32":
    import ctypes
import sv_ttk
from watchdog.events import FileSystemEventHandler

# --- Custom Formatter for logging ---
class CustomFormatter(logging.Formatter):
    """A custom formatter that allows for timestamp-free messages."""
    def format(self, record):
        if hasattr(record, 'plain') and record.plain:
            return record.getMessage()
        return super().format(record)

# --- Basic Setup ---
# Configure logging to provide clear feedback to the console.
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s', # Format is now handled by the custom formatter
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Core Backup and Cleanup Functions ---

def enforce_retention_policy(backup_folder, source_folder_name, versions_to_keep):
    """Deletes the oldest backups if the count exceeds the specified limit."""
    try:
        logging.info(f"🔎 Checking retention policy ({versions_to_keep} versions to keep)...")
        # Find all backup files for the specific source folder
        all_backups = [
            f for f in os.listdir(backup_folder)
            if f"_{source_folder_name}_" in f and f.endswith(".zip")
        ]

        # Sort files chronologically based on the timestamp in the name
        all_backups.sort()

        # If the number of backups exceeds the limit, delete the oldest ones
        if len(all_backups) > versions_to_keep:
            num_to_delete = len(all_backups) - versions_to_keep
            logging.info(f"ℹ️ Found {len(all_backups)} backups. Deleting the {num_to_delete} oldest one(s).")
            for i in range(num_to_delete):
                file_to_delete = os.path.join(backup_folder, all_backups[i])
                os.remove(file_to_delete)
                logging.info(f"🗑️ Deleted old backup: {file_to_delete}")
    except Exception as e:
        logging.error(f"Error enforcing retention policy: {e}")

def create_backup(source_folder, backup_folder, versions_to_keep, ignore_shader_cache, backup_type="Other Backup", backup_suffix="Other", debug_output=False):
    """Creates a timestamped zip archive and then enforces the retention policy."""
    try:
        source_folder_name = os.path.basename(os.path.normpath(source_folder))
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        archive_name = f"{timestamp}_{source_folder_name}_{backup_suffix}"
        archive_path_base = os.path.join(backup_folder, archive_name)
        archive_path = f"{archive_path_base}.zip"

        logging.info(f"⚙️ Starting {backup_type} for '{source_folder}'...")

        # Manually create the zip file to allow for folder exclusion
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(source_folder):
                # If ignore_shader_cache is True and 'cache' is in the list of directories,
                # remove it from 'dirs' so os.walk() will not traverse into it.
                if ignore_shader_cache and 'cache' in dirs:
                    if debug_output:
                        logging.info(f"Ignoring 'cache' directory in {root}")
                    dirs.remove('cache')

                for file in files:
                    file_path = os.path.join(root, file)
                    # The arcname is the path that the file will have inside the zip archive.
                    # os.path.relpath ensures the paths are relative to the source folder.
                    arcname = os.path.relpath(file_path, source_folder)
                    zipf.write(file_path, arcname)

        logging.info(f"💾 Successfully created backup: {archive_path}")

        # After a successful backup, clean up old versions
        enforce_retention_policy(backup_folder, source_folder_name, versions_to_keep)

    except Exception as e:
        logging.error(f"Failed to create backup: {e}")

# --- Watchdog Event Handler ---

class BackupEventHandler(FileSystemEventHandler):
    """
    Handles file system events and triggers the backup process after a period of inactivity.
    This "debouncing" prevents multiple backups during a single large file operation.
    """
    def __init__(self, config, app_instance):
        self.config = config
        self.timer = None
        self.change_detected_in_batch = False
        self.changed_events = [] # Store (event_type, src_path) tuples
        # Time to wait in seconds after the last detected change before starting a backup
        self.DEBOUNCE_SECONDS = 5.0

    def _handle_event(self, event):
        if os.path.realpath(event.src_path).startswith(os.path.realpath(self.config['backup_folder'])):
            return

        # If ignoring shader cache, check if the event path is inside a 'cache' directory.
        if self.config.get('ignore_shader_cache', True):
            # Split the path into components and check if 'cache' is one of them.
            # We use realpath to resolve symlinks and normalize path separators.
            path_parts = os.path.realpath(event.src_path).split(os.sep)
            if 'cache' in path_parts:
                return # Ignore event inside a cache folder

        if not event.is_directory: # We only care about file changes
            self.changed_events.append((event.event_type, event.src_path))

        # Check the debug setting from the app instance to allow runtime changes
        if self.config.get('debug_output', False):
            logging.info(f"Change detected: {event.event_type} at {event.src_path}. Resetting timer.")
        self.change_detected_in_batch = True

        # If a timer is already running, cancel it to reset the waiting period
        if self.timer is not None:
            self.timer.cancel()

        # Start a new timer that will trigger the backup function
        self.timer = threading.Timer(self.DEBOUNCE_SECONDS, self._trigger_backup)
        self.timer.start()

    def on_modified(self, event):
        """Called when a file or directory is modified."""
        self._handle_event(event)

    def on_created(self, event):
        """Called when a file or directory is created."""
        self._handle_event(event)

    def on_deleted(self, event):
        """Called when a file or directory is deleted."""
        self._handle_event(event)

    def on_moved(self, event):
        """Called when a file or directory is moved or renamed."""
        self._handle_event(event)

    def _trigger_backup(self):
        """The actual function called by the timer after the debounce period."""
        # If not in debug mode, show a single notification that changes were detected.
        if not self.config.get('debug_output', False) and self.change_detected_in_batch:
            logging.info("⚠️ Change detected. Preparing backup...")
        self.change_detected_in_batch = False # Reset for next batch

        # --- Overhauled Classification Logic ---
        backup_type = "Other Backup"
        backup_suffix = "Other"
        always_backup = False

        # Rule 1: Check for true deletions. A file overwrite often involves a delete followed by a rename.
        # We only classify as "Undelete" if a deleted file does not exist at the end of the debounce period.
        deleted_paths = [path for event_type, path in self.changed_events if event_type == 'deleted']
        is_true_deletion = any(not os.path.exists(path) for path in deleted_paths)

        if is_true_deletion:
            backup_type = "Undelete Backup"
            backup_suffix = "Undelete"
            always_backup = True
        else:
            # Analyze all changed .hg files
            hg_files = [path for _, path in self.changed_events if path.lower().endswith('.hg')]
            save_hg_files = [path for path in hg_files if "save" in os.path.basename(path).lower()]

            if not hg_files:
                # No .hg files changed, must be "Other"
                pass # Defaults are already "Other"
            elif not save_hg_files:
                # Rule 2: .hg files exist, but none are "save" files.
                pass # Defaults are already "Other"
            else:
                # At least one "save.hg" file was changed. Now analyze them.
                has_even = False
                has_odd_or_none = False

                for path in save_hg_files:
                    filename = os.path.basename(path).lower()
                    numbers = ''.join(filter(str.isdigit, filename))
                    if not numbers:
                        has_odd_or_none = True
                    else:
                        try:
                            if int(numbers) % 2 == 0:
                                has_even = True
                            else:
                                has_odd_or_none = True
                        except ValueError:
                            pass # Should not happen

                # Rule 5: A mix of save types.
                if has_even and has_odd_or_none:
                    backup_type = "General Savegame Backup"
                    backup_suffix = "General"
                    always_backup = True
                # Rule 3: Only even-numbered saves.
                elif has_even and not has_odd_or_none:
                    backup_type = "Restore Point Backup"
                    backup_suffix = "RestorePoint"
                # Rule 4: Only odd or no-number saves.
                elif has_odd_or_none and not has_even:
                    backup_type = "Autosave Backup"
                    backup_suffix = "AutoSave"

        self.changed_events.clear() # Reset for the next cycle

        # Check configuration to see if this type of backup is enabled
        if always_backup:
            pass # This backup type must always run.
        elif backup_suffix == "RestorePoint" and not self.config.get("backup_restore_points", True):
            logging.info("🚫 Restore Point backup is disabled in settings. Skipping.")
            return
        elif backup_suffix == "AutoSave" and not self.config.get("backup_autosaves", True):
            logging.info("🚫 Autosave backup is disabled in settings. Skipping.")
            return
        elif backup_suffix == "Other" and not self.config.get("backup_other", True):
            logging.info("🚫 'Other' file change backup is disabled in settings. Skipping.")
            return

        if self.config.get('debug_output', False):
            logging.info("Inactivity detected. Proceeding with backup...")

        # Check if source folder still exists before creating backup
        source_folder = self.config.get('source_folder')
        if not source_folder or not os.path.isdir(source_folder):
            logging.error(f"Source folder '{source_folder}' not found. Skipping backup.")
            return

        create_backup(
            source_folder,
            self.config['backup_folder'],
            self.config['versions_to_keep'],
            self.config.get('ignore_shader_cache', True),
            backup_type=backup_type,
            backup_suffix=backup_suffix,
            debug_output=self.config.get('debug_output', False)
        )
        logging.info("✅ Backup process complete. Awaiting next change.")
        logging.info('-' * 60, extra={'plain': True})

# --- GUI Helper Classes ---

class Tooltip:
    """
    Creates a tooltip for a given widget that only shows when the widget is disabled.
    """
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event=None):
        """Display the tooltip window."""
        # Only show the tooltip if the widget is disabled.
        if str(self.widget.cget('state')) == 'disabled':
            x, y, _, _ = self.widget.bbox("insert")
            x += self.widget.winfo_rootx() + 25
            y += self.widget.winfo_rooty() + 25

            self.tooltip_window = tk.Toplevel(self.widget)
            self.tooltip_window.wm_overrideredirect(True)
            self.tooltip_window.wm_geometry(f"+{x}+{y}")

            # Check current theme to set appropriate tooltip colors
            if sv_ttk.get_theme() == "dark":
                bg_color = "#3c3c3c"
                fg_color = "#ffffff"
                border_color = "#6e6e6e"
            else:  # light theme
                bg_color = "#ffffe0"
                fg_color = "#000000"
                border_color = "#aaaaaa"

            label = ttk.Label(self.tooltip_window, text=self.text, justify='left',
                              background=bg_color, foreground=fg_color,
                              relief='solid', borderwidth=1,
                              font=("tahoma", "8", "normal"), padding=4)
            label.configure(style='Tooltip.TLabel')
            label.pack(ipadx=1)

    def hide_tooltip(self, event=None):
        """Destroy the tooltip window."""
        if self.tooltip_window:
            self.tooltip_window.destroy()
        self.tooltip_window = None

# --- GUI Application ---

class QueueHandler(logging.Handler):
    """A custom logging handler that puts messages into a queue."""
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))

class BackupApp:
    """The main GUI application class."""
    def __init__(self, root, config_path):
        self.root = root
        self.config_path = config_path
        self.observer = None
        self.config = {}

        self.root.title("Atlas Archive: No Man's Sky automatic savegame backups")
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        # --- Load Config and Apply Theme ---
        self.load_config()

        # --- GUI Widgets ---
        self.create_widgets()
        self.populate_widgets_from_config()

        # --- Logging to GUI ---
        self.log_queue = queue.Queue()
        self.queue_handler = QueueHandler(self.log_queue)
        # Create a specific formatter for the GUI log to ensure timestamps are included
        gui_formatter = CustomFormatter(
            '[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.queue_handler.setFormatter(gui_formatter)
        logging.getLogger().addHandler(self.queue_handler)
        self.root.after(100, self.process_log_queue)

        # Delay initial theme application to ensure the window is ready
        self.root.after(50, lambda: self.apply_theme(self.config.get("theme", "light")))

        # --- Autostart Logic ---
        if self.autostart_var.get():
            logging.info("Autostart is enabled. Starting monitoring...")
            # Use 'after' to ensure the GUI is fully drawn before starting
            self.root.after(200, self.start_monitoring)

    def create_widgets(self):
        """Create and layout all the GUI widgets."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # --- Configuration Frame ---
        # --- Status Indicator Frame ---
        status_indicator_frame = ttk.Frame(main_frame, padding=(0, 0, 0, 10))
        status_indicator_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E))
        status_indicator_frame.columnconfigure(1, weight=1) # Make the middle column expandable

        # Canvas for the dot matrix display
        self.status_canvas = tk.Canvas(status_indicator_frame, width=20, height=20, highlightthickness=0, borderwidth=0)
        self.status_canvas.grid(row=0, column=0, padx=(0, 10))

        # The circle (light) on the canvas
        self.status_light = self.status_canvas.create_oval(3, 3, 18, 18, outline="", width=2)

        self.status_label_var = tk.StringVar()
        status_label = ttk.Label(status_indicator_frame, textvariable=self.status_label_var, font=("-size 12 -weight bold"))
        status_label.grid(row=0, column=1, sticky=tk.W)

        self.theme_button = ttk.Button(status_indicator_frame, text="Toggle Theme", command=self.toggle_theme)
        self.theme_button.grid(row=0, column=2, padx=5)

        config_frame = ttk.LabelFrame(main_frame, text="Configuration", padding="10")
        config_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E))
        config_frame.columnconfigure(0, weight=1) # Make the child frames expandable

        # --- Settings Frame (for paths and versions) ---
        settings_frame = ttk.LabelFrame(config_frame, text="Settings", padding="10")
        settings_frame.grid(row=0, column=0, columnspan=4, sticky="ew")
        settings_frame.columnconfigure(1, weight=1) # Make the entry widget column expandable

        ttk.Label(settings_frame, text="Source Folder:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.source_var = tk.StringVar()
        self.source_entry = ttk.Entry(settings_frame, textvariable=self.source_var)
        Tooltip(self.source_entry, "Stop monitoring to change this setting.")
        self.source_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5)
        self.source_browse_button = ttk.Button(settings_frame, text="Browse...", command=lambda: self.browse_folder(self.source_var))
        Tooltip(self.source_browse_button, "Stop monitoring to change this setting.")
        self.source_browse_button.grid(row=0, column=2, padx=(0, 5))
        self.source_open_button = ttk.Button(settings_frame, text="Open", command=lambda: self.open_folder_in_explorer(self.source_var.get()), width=5)
        self.source_open_button.grid(row=0, column=3)

        ttk.Label(settings_frame, text="Destination Folder:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.backup_var = tk.StringVar()
        self.backup_entry = ttk.Entry(settings_frame, textvariable=self.backup_var)
        Tooltip(self.backup_entry, "Stop monitoring to change this setting.")
        self.backup_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5)
        self.backup_browse_button = ttk.Button(settings_frame, text="Browse...", command=lambda: self.browse_folder(self.backup_var))
        Tooltip(self.backup_browse_button, "Stop monitoring to change this setting.")
        self.backup_browse_button.grid(row=1, column=2, padx=(0, 5))
        self.backup_open_button = ttk.Button(settings_frame, text="Open", command=lambda: self.open_folder_in_explorer(self.backup_var.get()), width=5)
        self.backup_open_button.grid(row=1, column=3)

        ttk.Label(settings_frame, text="Maximum number of backups:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.versions_var = tk.IntVar()
        self.versions_spinbox = ttk.Spinbox(settings_frame, from_=1, to=100, textvariable=self.versions_var, width=5)
        Tooltip(self.versions_spinbox, "Stop monitoring to change this setting.")
        self.versions_spinbox.grid(row=2, column=1, sticky=tk.W, padx=5)

        # --- Options Frame for responsive layout ---
        self.options_frame = ttk.LabelFrame(config_frame, text="Options", padding="10")
        self.options_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 2))

        self.autostart_var = tk.BooleanVar()
        self.autostart_check = ttk.Checkbutton(self.options_frame, text="Autostart Monitoring on Launch", variable=self.autostart_var)
        Tooltip(self.autostart_check, "Stop monitoring to change this setting.")

        self.ignore_cache_var = tk.BooleanVar()
        self.ignore_cache_check = ttk.Checkbutton(self.options_frame, text="Ignore Shader Cache ('cache' folders)", variable=self.ignore_cache_var)
        Tooltip(self.ignore_cache_check, "Stop monitoring to change this setting.")

        self.debug_output_var = tk.BooleanVar()
        self.debug_output_check = ttk.Checkbutton(self.options_frame, text="Debug output", variable=self.debug_output_var, command=self.on_debug_toggle)
        Tooltip(self.debug_output_check, "Stop monitoring to change this setting.")

        # Bind the frame's resize event to the layout update function
        self.options_frame.bind("<Configure>", self.update_checkbox_layout)
        # A flag to prevent redundant re-gridding
        self.checkboxes_are_stacked = None

        # --- Backup Triggers Frame ---
        triggers_frame = ttk.LabelFrame(config_frame, text="Backup Triggers", padding="10")
        triggers_frame.grid(row=2, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=(10, 0))
        triggers_frame.columnconfigure(3, weight=1) # Spacer column

        self.backup_autosaves_var = tk.BooleanVar()
        self.backup_autosaves_check = ttk.Checkbutton(triggers_frame, text="Autosaves", variable=self.backup_autosaves_var)
        Tooltip(self.backup_autosaves_check, "Stop monitoring to change this setting.")
        self.backup_autosaves_check.grid(row=0, column=0)
        self.backup_restore_points_var = tk.BooleanVar()
        self.backup_restore_points_check = ttk.Checkbutton(triggers_frame, text="Restore Points", variable=self.backup_restore_points_var)
        Tooltip(self.backup_restore_points_check, "Stop monitoring to change this setting.")
        self.backup_restore_points_check.grid(row=0, column=1, padx=15)
        self.backup_other_var = tk.BooleanVar()
        self.backup_other_check = ttk.Checkbutton(triggers_frame, text="Other Changes", variable=self.backup_other_var)
        Tooltip(self.backup_other_check, "Stop monitoring to change this setting.")
        self.backup_other_check.grid(row=0, column=2)

        # --- Control Frame ---
        control_frame = ttk.Frame(main_frame, padding="10")
        control_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E))
        control_frame.columnconfigure((0, 1), weight=1)

        # Create a custom style for the toggle button to left-align the text
        # and remove the focus border.
        style = ttk.Style(self.root)
        style.configure('Toggle.TButton', anchor='w')
        style.layout('Toggle.TButton',
             [('Button.border', {'border': '1', 'children':
                 [('Button.padding', {'children': [('Button.label', {'side': 'left', 'expand': 1})]})]})])

        self.toggle_monitoring_button = ttk.Button(control_frame, text="▶ Start Monitoring", command=self.toggle_monitoring, style='Toggle.TButton')
        self.toggle_monitoring_button.grid(row=0, column=0, padx=5, pady=15)

        self.quit_button = ttk.Button(control_frame, text="Save & Quit", command=self.quit_app)
        self.quit_button.grid(row=0, column=1, padx=5, pady=15)

        # --- Status Frame ---
        status_frame = ttk.LabelFrame(main_frame, text="Status Log", padding="10")
        status_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        main_frame.rowconfigure(3, weight=1)
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(status_frame, state='disabled', height=10, font=("TkDefaultFont", 12), spacing3=4)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # --- Disclaimer ---
        separator = ttk.Separator(main_frame, orient='horizontal')
        separator.grid(row=4, column=0, columnspan=3, sticky='ew', pady=5)

        disclaimer_text = ("This tool has no affiliation with \"No Man's Sky\" or \"Hello Games\". | "
                         "Use at your own risk. The author is not responsible for any damage or data loss.")
        disclaimer_label = ttk.Label(main_frame, text=disclaimer_text, justify=tk.CENTER, font=("-size 8"), anchor=tk.CENTER)
        disclaimer_label.grid(row=5, column=0, columnspan=3, sticky='ew')

        # Set initial status indicator state
        self.update_status_indicator(False)

    def process_log_queue(self):
        """Checks the queue for new log messages and adds them to the GUI."""
        try:
            while True:
                record = self.log_queue.get_nowait()
                self.log_text.config(state='normal')
                self.log_text.insert(tk.END, record + '\n')
                self.log_text.see(tk.END)
                self.log_text.config(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.process_log_queue)

    def browse_folder(self, path_var):
        """Open a folder browser dialog and update the string variable."""
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            path_var.set(folder_selected)

    def open_folder_in_explorer(self, path):
        """Opens the given path in the system's file explorer."""
        if not path or not os.path.isdir(path):
            messagebox.showwarning("Path Not Found", f"The folder does not exist or is not specified:\n{path}")
            return

        try:
            # os.startfile is Windows-only
            if sys.platform == "win32":
                os.startfile(os.path.realpath(path))
            # For macOS
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            # For Linux and other Unix-like OS
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            logging.error(f"Failed to open folder '{path}': {e}")
            messagebox.showerror("Error", f"Could not open folder:\n{e}")

    def update_status_indicator(self, is_active: bool):
        """Updates the color and text of the status indicator."""
        if is_active:
            color = "#2ECC71"  # A nice flat green
            text = "Atlas Archive active"
        else:
            color = "#E74C3C"  # A nice flat red
            text = "Atlas Archive inactive"

        self.status_label_var.set(text)

        # Update canvas background to match the root window's background
        # This ensures it looks correct in both light and dark themes
        self.status_canvas.config(bg=self.root.cget("background"))

        # Update the fill color of the status light
        self.status_canvas.itemconfig(self.status_light, fill=color)

    def update_checkbox_layout(self, event):
        """Dynamically stacks or un-stacks checkboxes based on frame width."""
        # Threshold width in pixels. If the frame is narrower than this, stack the widgets.
        # This value may need tweaking based on font sizes and checkbox text length.
        threshold_width = 650

        # Check if the layout needs to change to avoid unnecessary processing
        should_stack = event.width < threshold_width

        if should_stack and self.checkboxes_are_stacked is not True:
            # Stack them vertically
            self.autostart_check.grid(row=0, column=0, sticky="w")
            self.ignore_cache_check.grid(row=1, column=0, sticky="w")
            self.debug_output_check.grid(row=2, column=0, sticky="w")
            # Remove column weights to prevent stretching
            self.options_frame.columnconfigure((0, 1, 2), weight=0)
            self.checkboxes_are_stacked = True

        elif not should_stack and self.checkboxes_are_stacked is not False:
            # Arrange them horizontally
            self.autostart_check.grid(row=0, column=0, sticky="w")
            self.ignore_cache_check.grid(row=0, column=1, sticky="w", padx=15)
            self.debug_output_check.grid(row=0, column=2, sticky="w", padx=15)
            # Set column weights to 0 for content, and 1 for a "spacer" column
            # to push everything to the left.
            self.options_frame.columnconfigure((0, 1, 2), weight=0)
            self.options_frame.columnconfigure(3, weight=1)
            self.checkboxes_are_stacked = False

    def load_config(self):
        """Loads configuration from the JSON file. Creates a default file if it doesn't exist."""
        default_config = {
            "source_folder": "",
            "backup_folder": "",
            "versions_to_keep": 50,
            "theme": "light",
            "autostart": False,
            "ignore_shader_cache": True,
            "debug_output": False,
            "backup_autosaves": True,
            "backup_restore_points": True,
            "backup_other": True
        }
        try:
            if not os.path.exists(self.config_path):
                logging.info(f"Config file not found. Creating a default one at: {self.config_path}")
                with open(self.config_path, 'w') as f:
                    json.dump(default_config, f, indent=2)
                self.config = default_config
            else:
                with open(self.config_path, 'r') as f:
                    loaded_config = json.load(f)
                # Merge with defaults to handle missing keys in older configs
                self.config = {**default_config, **loaded_config}

        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Error processing config file: {e}")
            messagebox.showerror("Config Error", f"Could not load or create config file: {e}\nUsing temporary default settings.")
            # Use default config in memory if file operations fail
            self.config = default_config

    def save_config(self):
        """Saves the current GUI settings to the config file."""
        self.config['source_folder'] = self.source_var.get()
        self.config['backup_folder'] = self.backup_var.get()
        self.config['versions_to_keep'] = self.versions_var.get()
        self.config['theme'] = sv_ttk.get_theme()
        self.config['autostart'] = self.autostart_var.get()
        self.config['ignore_shader_cache'] = self.ignore_cache_var.get()
        self.config['debug_output'] = self.debug_output_var.get()
        self.config['backup_autosaves'] = self.backup_autosaves_var.get()
        self.config['backup_restore_points'] = self.backup_restore_points_var.get()
        self.config['backup_other'] = self.backup_other_var.get()
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
            logging.info(f"Configuration saved to {self.config_path}")
        except Exception as e:
            logging.error(f"Failed to save configuration: {e}")

    def populate_widgets_from_config(self):
        self.source_var.set(self.config.get('source_folder', ''))
        self.backup_var.set(self.config.get('backup_folder', ''))
        self.versions_var.set(self.config.get('versions_to_keep', 50))
        self.autostart_var.set(self.config.get('autostart', False))
        self.ignore_cache_var.set(self.config.get('ignore_shader_cache', True))
        self.debug_output_var.set(self.config.get('debug_output', False))
        self.backup_autosaves_var.set(self.config.get('backup_autosaves', True))
        self.backup_restore_points_var.set(self.config.get('backup_restore_points', True))
        self.backup_other_var.set(self.config.get('backup_other', True))

    def on_debug_toggle(self):
        """Called when the debug checkbox is toggled."""
        self.config['debug_output'] = self.debug_output_var.get()
        logging.info(f"Debug output set to: {self.config['debug_output']}")

    def apply_theme(self, theme_name):
        """Applies the specified theme and updates the title bar."""
        is_dark = (theme_name == "dark")
        sv_ttk.set_theme(theme_name)
        self._update_title_bar_theme(is_dark)
        # The observer might not be running, so we need to know its state
        is_active = self.observer and self.observer.is_alive()
        self.update_status_indicator(is_active)

    def _update_title_bar_theme(self, is_dark: bool):
        """
        On Windows, sets the title bar to dark or light mode to match the theme.
        This uses a Windows-specific API call and has no effect on other OSes.
        """
        if sys.platform == "win32":
            try:
                # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (for Windows 10 1903+ and Windows 11)
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20

                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                value = ctypes.c_int(2 if is_dark else 0) # 2 = Enable, 0 = Disable

                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
            except Exception as e:
                logging.warning(f"Could not set title bar theme: {e}")

    def toggle_theme(self):
        """Switches the GUI theme between light and dark."""
        new_theme = "dark" if sv_ttk.get_theme() == "light" else "light"
        self.apply_theme(new_theme)

    def toggle_monitoring(self):
        """Starts or stops monitoring based on the current state."""
        if self.observer and self.observer.is_alive():
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def _set_config_widgets_state(self, state: str):
        """Sets the state of all configuration widgets. 'normal' or 'disabled'."""
        widgets = [
            self.source_entry, self.source_browse_button,
            self.backup_entry, self.backup_browse_button,
            self.versions_spinbox,
            self.autostart_check,
            self.ignore_cache_check,
            self.backup_autosaves_check,
            self.backup_restore_points_check,
            self.backup_other_check
        ]
        for widget in widgets:
            if widget:
                widget.config(state=state)

    def start_monitoring(self):
        self.save_config() # Save current settings before starting

        # Validate paths
        if not os.path.isdir(self.config['source_folder']):
            logging.error(f"Source folder does not exist: {self.config['source_folder']}")
            messagebox.showerror("Error", f"Source folder does not exist:\n{self.config['source_folder']}")
            return

        os.makedirs(self.config['backup_folder'], exist_ok=True)

        self.event_handler = BackupEventHandler(self.config, self)
        self.observer = Observer()
        self.observer.schedule(self.event_handler, self.config['source_folder'], recursive=True)
        self.observer.start()

        self.toggle_monitoring_button.config(text="■ Stop Monitoring")
        self._set_config_widgets_state(tk.DISABLED)
        self.update_status_indicator(True)
        logging.info(f"✅ Watchdog started. Monitoring folder: {self.config['source_folder']}")

    def stop_monitoring(self):
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
            self.observer = None
            logging.info("🛑 Watchdog stopped.")

        self.toggle_monitoring_button.config(text="▶ Start Monitoring")
        self._set_config_widgets_state(tk.NORMAL)
        self.update_status_indicator(False)

    def quit_app(self):
        self.stop_monitoring()
        self.save_config()
        self.root.destroy()

# --- Main Execution ---

def run_console_mode(config_path):
    """Runs the backup monitor in a console-only (no GUI) mode."""
    logging.info("Running in console-only mode.")

    # Simplified config loading for console mode
    config = {}
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        logging.info(f"Configuration loaded from {config_path}")
    except (IOError, json.JSONDecodeError) as e:
        logging.error(f"Fatal: Could not load config file '{config_path}': {e}")
        sys.exit(1)

    # Validate paths
    source_folder = config.get('source_folder')
    backup_folder = config.get('backup_folder')

    if not source_folder or not os.path.isdir(source_folder):
        logging.error(f"Source folder does not exist or is not specified in config: {source_folder}")
        sys.exit(1)

    os.makedirs(backup_folder, exist_ok=True)

    # The event handler doesn't need the app instance for console mode
    event_handler = BackupEventHandler(config, None)
    observer = Observer()
    observer.schedule(event_handler, source_folder, recursive=True)
    observer.start()

    logging.info(f"✅ Watchdog started. Monitoring folder: {source_folder}")
    logging.info("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("🛑 Ctrl+C received. Stopping watchdog...")
        observer.stop()
    observer.join()
    logging.info("Watchdog stopped. Exiting.")

def setup_plain_console_logging():
    """Sets up the root logger to use the custom formatter for console output."""
    root_logger = logging.getLogger()
    # Replace the default handler's formatter with our custom one
    root_logger.handlers[0].setFormatter(CustomFormatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))

def main():
    """Parses arguments, loads config, and starts the file system observer."""
    # Print disclaimer to console on every launch
    disclaimer = ("This tool has no affiliation with \"No Man's Sky\" or \"Hello Games\".\n"
                  "This software is provided 'as-is'. Use at your own risk. "
                  "The author is not responsible for any damage or data loss.")
    print(f"\n{disclaimer}\n")

    setup_plain_console_logging()

    parser = argparse.ArgumentParser(description="A tool to automatically back up a folder when its contents change.")
    parser.add_argument("config_file", help="Path to the JSON configuration file.")
    parser.add_argument("--nogui", action="store_true", help="Run in console-only mode without a GUI.")
    args = parser.parse_args()

    if args.nogui:
        run_console_mode(args.config_file)
    else:
        root = tk.Tk()

        # Set initial window size to a percentage of the screen
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        width = int(screen_width * 0.75)
        height = int(screen_height * (2 / 3))
        root.geometry(f"{width}x{height}")

        app = BackupApp(root, args.config_file)
        root.mainloop()

if __name__ == "__main__":
    main()