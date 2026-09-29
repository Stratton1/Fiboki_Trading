#!/usr/bin/env bash
# Installs "Fiboki.app" (a double-click launcher) and "Start Fiboki.command" on the Desktop.
# Run from the repository root on the Mac:  scripts/desktop/install-launcher.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
DESK="${1:-$HOME/Desktop}"
APP="$DESK/Fiboki.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
sed "s#__FIBOKI_ROOT__#$ROOT#" "scripts/desktop/Start Fiboki.command" > "$DESK/Start Fiboki.command"
chmod +x "$DESK/Start Fiboki.command"
# The app just opens the .command in Terminal so the operator sees the logs.
cat > "$APP/Contents/MacOS/Fiboki" <<LAUNCH
#!/usr/bin/env bash
open -a Terminal "$DESK/Start Fiboki.command"
LAUNCH
chmod +x "$APP/Contents/MacOS/Fiboki"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Fiboki</string>
  <key>CFBundleDisplayName</key><string>Fiboki</string>
  <key>CFBundleIdentifier</key><string>uk.fiboki.launcher</string>
  <key>CFBundleVersion</key><string>2.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>Fiboki</string>
  <key>CFBundleIconFile</key><string>Fiboki.icns</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict></plist>
PLIST
if [ ! -f scripts/desktop/Fiboki.icns ] && command -v iconutil >/dev/null; then iconutil -c icns scripts/desktop/Fiboki.iconset -o scripts/desktop/Fiboki.icns; fi
if [ -f scripts/desktop/Fiboki.icns ]; then cp scripts/desktop/Fiboki.icns "$APP/Contents/Resources/Fiboki.icns"; fi
touch "$APP"
echo "Installed: $APP and $DESK/Start Fiboki.command (repo: $ROOT)"
