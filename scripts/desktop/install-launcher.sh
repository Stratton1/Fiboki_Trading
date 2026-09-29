#!/usr/bin/env bash
# Installs "Fiboki.app" on the Desktop: a real app bundle that starts the
# launchd services silently and opens the workstation (scripts/desktop/
# fiboki-launch.sh). Also installs "Start Fiboki (Terminal).command" for the
# interactive dev-up path with visible logs.
#
#   scripts/desktop/install-launcher.sh                 # from the runtime checkout
#   scripts/desktop/install-launcher.sh --root ~/fiboki # or name it
#   scripts/desktop/install-launcher.sh --desktop DIR   # install elsewhere (tests)
#
# The icon is rebuilt from scripts/desktop/Fiboki.iconset with iconutil on
# every install, so a regenerated iconset lands without a manual step.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
DESK="$HOME/Desktop"
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$(cd "${2:?}" && pwd)"; shift 2 ;;
    --desktop) DESK="${2:?}"; shift 2 ;;
    *) DESK="$1"; shift ;;  # legacy positional
  esac
done
case "$ROOT" in *'#'*) echo "repository path $ROOT contains '#', which sed cannot substitute" >&2; exit 2 ;; esac
[ -f "$ROOT/scripts/fiboki-service.sh" ] || { echo "not a Fiboki checkout: $ROOT" >&2; exit 2; }
case "$ROOT" in
  "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
    echo "WARNING: $ROOT is under a macOS-protected folder; launchd services cannot read it" >&2
    echo "         (DEPLOYMENT.md 2.6). Install from the runtime checkout: --root ~/fiboki" >&2 ;;
esac

APP="$DESK/Fiboki.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed "s#__FIBOKI_ROOT__#$ROOT#g" scripts/desktop/fiboki-launch.sh > "$APP/Contents/MacOS/Fiboki"
chmod +x "$APP/Contents/MacOS/Fiboki"
sed "s#__FIBOKI_ROOT__#$ROOT#g" "scripts/desktop/Start Fiboki.command" > "$DESK/Start Fiboki (Terminal).command"
chmod +x "$DESK/Start Fiboki (Terminal).command"
rm -f "$DESK/Start Fiboki.command"  # the pre-2026-09-30 name

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Fiboki</string>
  <key>CFBundleDisplayName</key><string>Fiboki</string>
  <key>CFBundleIdentifier</key><string>uk.fiboki.launcher</string>
  <key>CFBundleVersion</key><string>2.1</string>
  <key>CFBundleShortVersionString</key><string>2.1</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>Fiboki</string>
  <key>CFBundleIconFile</key><string>Fiboki</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>Fiboki. Paper mode only.</string>
</dict></plist>
PLIST
printf 'APPL????' > "$APP/Contents/PkgInfo"

if command -v iconutil >/dev/null 2>&1; then
  iconutil -c icns scripts/desktop/Fiboki.iconset -o "$APP/Contents/Resources/Fiboki.icns"
elif [ -f scripts/desktop/Fiboki.icns ]; then
  cp scripts/desktop/Fiboki.icns "$APP/Contents/Resources/Fiboki.icns"
else
  echo "no iconutil and no prebuilt Fiboki.icns: the app gets the generic icon" >&2
fi
# Finder caches icons by bundle; touching the bundle and re-registering it
# with LaunchServices makes the new icon show without a logout.
touch "$APP" "$APP/Contents/Info.plist"
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREG" ] && "$LSREG" -f "$APP" >/dev/null 2>&1 || true
echo "Installed: $APP (runtime: $ROOT)"
echo "           $DESK/Start Fiboki (Terminal).command (dev-up with visible logs)"
