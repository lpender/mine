# Discord Monitor

BetterDiscord plugin for monitoring stock alerts and forwarding them to the trading server.

## Components

- **StockAlertMonitor.plugin.js** - BetterDiscord plugin that monitors Discord channels for stock alerts
- **alert_server.py** - HTTP server that receives alerts from the plugin (runs as part of the trading system)

## Installation

### 1. Install BetterDiscord
Download from https://betterdiscord.app/

### 2. Install the Plugin
Copy the plugin to your BetterDiscord plugins folder:

```bash
# macOS
cp StockAlertMonitor.plugin.js ~/Library/Application\ Support/BetterDiscord/plugins/

# Windows
cp StockAlertMonitor.plugin.js %appdata%/BetterDiscord/plugins/

# Linux
cp StockAlertMonitor.plugin.js ~/.config/BetterDiscord/plugins/
```

### 3. Enable the Plugin
1. Open Discord
2. Go to User Settings > Plugins
3. Enable "StockAlertMonitor"

## Usage

### Enable Channels for Live Trading
1. Navigate to a Discord channel you want to monitor
2. Click the toggle button in the widget (bottom-right corner)
3. Enabled channels show a 📈 emoji indicator in the sidebar

### Manual Alert Sending
Each stock alert message gets a `▶ TICKER` button. Click to manually send an alert to the trading server.

### Backfill Historical Data
1. Scroll through messages in a channel
2. Click "Send Data" in the widget to send visible messages to the backfill endpoint

## Updating the Plugin

To check for differences between your development version and installed version:

```bash
# macOS
diff discord-monitor/StockAlertMonitor.plugin.js ~/Library/Application\ Support/BetterDiscord/plugins/StockAlertMonitor.plugin.js

# To update
cp discord-monitor/StockAlertMonitor.plugin.js ~/Library/Application\ Support/BetterDiscord/plugins/
```

Then reload the plugin in Discord (disable and re-enable, or restart Discord).

## Troubleshooting

### Plugin Not Forwarding Alerts Overnight

**Symptom:** Alerts appear in Discord but don't reach the trading server when computer is left unattended.

**Causes:**

1. **macOS App Nap** - macOS suspends "inactive" apps to save power, even with sleep disabled.
   ```bash
   # Disable App Nap for Discord
   defaults write com.hnc.Discord NSAppSleepDisabled -bool YES
   ```

2. **Discord WebSocket Reconnection** - Discord's connection drops periodically. When it reconnects, the plugin's message subscription may not survive.

3. **Background Tab Throttling** - Electron/Chromium throttles JavaScript in background windows.

**Solutions:**
- Disable App Nap (see above)
- Keep Discord window visible and focused
- Use a separate monitor or active virtual desktop for Discord
- Consider running a server-side Discord bot instead of relying on BetterDiscord

### Checking if Plugin is Working

1. Open Discord DevTools: `Cmd+Option+I` (Mac) or `Ctrl+Shift+I` (Windows)
2. Check console for `[StockAlertMonitor]` messages
3. Verify enabled channels:
   ```javascript
   BdApi.Data.load("StockAlertMonitor", "settings")
   ```

### Channel Indicators (📈) Not Showing

The plugin should show a 📈 emoji next to channels enabled for live trading. If they're not appearing:

**Debug Steps:**

1. **Check console logs** (Cmd+Option+I on Mac):
   ```javascript
   // Look for these messages:
   [StockAlertMonitor] [Indicators] Found X channel links
   [StockAlertMonitor] [Indicators] Enabled channels: [...]
   [StockAlertMonitor] [Indicators] Channel XXX is ENABLED
   [StockAlertMonitor] [Indicators] Added indicator to channel XXX
   ```

2. **Verify channels are actually enabled:**
   ```javascript
   // In Discord console:
   BdApi.Data.load("StockAlertMonitor", "settings")
   // Should show: { enabledChannels: ["123456789", ...], ... }
   ```

3. **Common issues:**
   - **No channels enabled** - Use Ctrl+Shift+T to toggle current channel
   - **Wrong channel IDs** - The channel IDs in settings may not match Discord's current IDs
   - **DOM selectors outdated** - Discord updated their UI structure
   - **Plugin not fully loaded** - Wait 1.5 seconds after enabling, or disable/re-enable

4. **Manual fix - get correct channel ID:**
   ```javascript
   // Navigate to a channel, then run in console:
   window.location.pathname.match(/\/channels\/\d+\/(\d+)/)[1]
   // This gives you the channel ID

   // Manually enable it:
   const settings = BdApi.Data.load("StockAlertMonitor", "settings");
   settings.enabledChannels.push("YOUR_CHANNEL_ID_HERE");
   BdApi.Data.save("StockAlertMonitor", "settings", settings);
   // Then disable/re-enable the plugin
   ```

Discord occasionally updates their DOM structure which can break the selectors. Check if the plugin needs updating for newer Discord versions.

### "Maximum call stack size exceeded" Error

If you see `RangeError: Maximum call stack size exceeded` in the BetterDiscord console:

**If error persists when StockAlertMonitor is disabled:**
- This is NOT caused by StockAlertMonitor
- The error message mentions "ThemeAttributes" which is BetterDiscord's theme system
- Likely caused by another plugin or theme creating recursive patches

**To troubleshoot:**
1. Disable all other plugins one by one to find the culprit
2. Try disabling your current theme
3. Check for BetterDiscord updates
4. Clear BetterDiscord cache: Delete `~/Library/Application Support/BetterDiscord/data/stable`

**If error only happens when StockAlertMonitor is enabled:**
- This was caused by a recursive loop in the channel indicator system (fixed in latest version)
- The indicator updates would trigger DOM mutations, which would trigger more updates

**Fix applied to StockAlertMonitor (v1.0+):**
- Added re-entrancy guard to prevent concurrent updates
- Modified MutationObserver to ignore self-caused mutations (indicator additions/removals)

**To update StockAlertMonitor:**
```bash
# Copy updated plugin to BetterDiscord
cp discord-monitor/StockAlertMonitor.plugin.js ~/Library/Application\ Support/BetterDiscord/plugins/

# Then reload Discord or disable/enable the plugin
```

## Configuration

Settings are stored in BetterDiscord's data store. Access via the plugin settings panel:

- **Alert webhook URL** - Where to send live alerts (default: `http://localhost:8765/alert`)
- **Backfill webhook URL** - Where to send historical data (default: `http://localhost:8765/backfill`)
- **Alert Sound Volume** - Volume slider (0-100%) for the alert sound (default: 70%)
  - Move the slider to test the volume - it will play a preview
  - Set to 0% to mute alerts completely
- **Enabled channels** - List of channel IDs enabled for live trading

## Alert Format

Alerts are sent as JSON POST requests:

```json
{
  "ticker": "BEAT",
  "price_info": "BEAT < $5",
  "channel": "nuntio-std",
  "content": "04:29  ↗  BEAT < $5  ·  1  NHOD  ~  :flag_us:  |  Float: 27.4 M  |  RVol: 25x  |  High CTB",
  "timestamp": "2025-12-17T09:29:00.000Z",
  "author": "NuntioBot"
}
```

## Known Limitations

- Requires Discord desktop app with BetterDiscord (won't work in browser)
- Plugin must be running on a machine that stays awake and has Discord focused
- Not suitable for production trading without additional reliability measures
