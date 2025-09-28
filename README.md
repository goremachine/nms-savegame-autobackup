# Atlas Archive: No Man's Sky automatic savegame backup utility

Atlas Archive is a tool that automatically monitors your No Man's Sky savegame folder and creates a compressed backup whenever it detects a change.

## Features

- **Automatic Backups**: Runs in the background and automatically creates a backup when a savegame file is modified.
- **Smart Debouncing**: Waits a few seconds after the last change to create a single backup, even during large save operations.
- **ZIP Compression**: Backups are stored as `.zip` files to save space.
- **Automatic Cleanup**: Enforces a retention policy, automatically deleting the oldest backups when the number of versions exceeds your configured limit.
- **User-Friendly GUI**: A simple interface to configure settings and view the status log.
- **Light & Dark Modes**: Includes a theme toggle for user comfort.
- **Console-Only Mode**: Can be run without a GUI using the `--nogui` flag, perfect for running as a background service or on a server.
- **Selective Backups**: Choose to back up on changes to Autosaves, Restore Points, or other miscellaneous file changes.

## Requirements

- **OS**: Only tested on Windows
- **Software**:
  - Git
  - Python 3.10+ (ensure you check "Add Python to PATH" during installation).
- **Python Packages**: `watchdog`, `sv-ttk`. These will be installed automatically by the startup script.

## Installation

1.  Open a command prompt (`cmd`) or terminal.
2.  Navigate to the directory where you want to install the tool (e.g., `cd C:\Tools`).
3.  Clone the repository from GitHub by running the following command:
    ```shell
    git clone https://github.com/mahammer/nms-savegame-autobackup.git
    ```

The first time you run the script, it will automatically create a Python virtual environment and install all the necessary dependencies. The GUI will then launch.

## How to Use

### First-Time Configuration

When you first launch the application, you will need to configure two important paths:

1.  **Source Folder**: This is your No Man's Sky savegame directory. Click "Browse..." and navigate to it. It is typically located at:
    `%APPDATA%\HelloGames\NMS\st_<your_steam_id>`
2.  **Destination Folder**: This is where you want to store your backups. It is highly recommended to choose a folder on a different physical drive.

Once configured, click "**Save & Quit**". The settings will be saved in `config.json`.

### Running the Application

You have two primary ways to run the tool:

- **With GUI**: Double-click `start_windows_gui.bat`. This is the recommended method for daily use. It starts the application with its graphical interface.
- **With Console Only**: Double-click `start_windows_nogui.bat`. This runs the application in a command prompt window without a graphical interface. All status messages will be printed to the console. Press `Ctrl+C` to stop it.

### Configuration Options

All settings can be configured via the GUI and are saved in the `config.json` file.

- **Source Folder**: The NMS savegame folder to monitor. The default path should be `%appdata%\HelloGames\NMS`. The utility does not work with %appdata% you need to provide the absolute path.
- **Destination Folder**: The folder where backups will be saved.
- **Maximum number of backups**: The number of backups to keep. The oldest backups will be deleted automatically.
- **Autostart Monitoring on Launch**: If checked, monitoring will begin as soon as the application starts.
- **Ignore Shader Cache**: If checked, changes inside any `cache` sub-folder will be ignored to prevent unnecessary backups.
- **Debug output**: If checked, provides verbose logging in the status window and console.
- **Backup Triggers**:
  - **Autosaves**: Trigger a backup when an autosave file is changed.
  - **Restore Points**: Trigger a backup when a manual save/restore point is changed.
  - **Other Changes**: Trigger a backup on any other file change in the source folder.

---

### Disclaimer

This tool has no affiliation with "No Man's Sky" or "Hello Games".

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
