#!/bin/bash
set -e

PROJECT_DIR="/Users/a1/Desktop/FKINGHDURunning"

open_terminal_window() {
  local title="$1"
  local command="$2"

  osascript <<OSA
tell application "Terminal"
  activate
  do script "printf '\\033]0;$title\\007'; $command"
end tell
OSA
}

open_terminal_window "pymobiledevice3 tunneld" "sudo python3 -m pymobiledevice3 remote tunneld"
open_terminal_window "iOS Location Injector" "cd '$PROJECT_DIR' && python3 ios_location_injector.py"

echo "已打开两个终端窗口："
echo "1. sudo python3 -m pymobiledevice3 remote tunneld"
echo "2. cd $PROJECT_DIR && python3 ios_location_injector.py"
