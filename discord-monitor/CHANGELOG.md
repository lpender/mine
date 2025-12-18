# StockAlertMonitor Plugin Changelog

## v2.4.0 - 2025-12-17

### Added
- **"Clear Sent History" button** in the widget
  - Red button below "Auto-Send" toggle
  - Clears the plugin's cache of sent message IDs
  - Shows count of cleared messages
  - Useful for re-processing messages with updated author extraction
  - Console logs show how many messages are ready to send after clearing

### Changed
- Enhanced `clearSentHistory()` function with better feedback:
  - Shows count of cleared messages in widget status
  - Displays success toast with message count
  - Logs to console for debugging
  - Auto-clears status message after 3 seconds

## v2.3.0 - 2025-12-17

### Fixed
- **Author extraction from Discord messages** (major fix)
  - Implemented multi-strategy DOM extraction approach
  - Strategy 1: Look for `h3[class*="header"]` → `span[class*="username"]`
  - Strategy 2: Direct `[class*="username_"]` search (fallback)
  - Strategy 3: Look in `[class*="headerText"]` container (fallback)
  - Now correctly captures authors like "PR - Spike", "Nuntiobot", "PR ↓ DROP", etc.
  - Previously all messages had empty author strings

### Added
- **Enhanced debug logging**:
  - Logs author changes as messages are parsed
  - Logs first 3 messages with author info
  - Logs total message count and unique author count
  - Logs author distribution when sending backfill
  - Example: `[StockAlertMonitor] Author change: "Nuntiobot" -> "PR - Spike"`

### Changed
- Version bumped to 2.3.0 to track the author extraction fix

## v2.2.0 and earlier

Previous versions - see git history for details.

## Upgrade Instructions

### From v2.3.0 to v2.4.0
1. Run: `task plugin:install`
2. Restart Discord (or press `Ctrl+R` to reload)
3. New "Clear Sent History" button will appear in the widget

### From v2.2.0 or earlier to v2.3.0+
1. Run: `task plugin:install`
2. **Fully quit and restart Discord** (`Cmd+Q` then reopen)
3. Clear sent history to re-process with correct authors:
   - Click the new "Clear Sent History" button, OR
   - In Discord console: `BdApi.Plugins.get("StockAlertMonitor").instance.clearSentHistory()`
4. Restart the trading engine to clear server-side cache
5. Scroll Discord or click "Send Data" to trigger backfill

## Known Issues

- Discord occasionally updates their DOM structure which can break author extraction selectors
- If authors appear empty again after a Discord update, the selectors may need updating
- Auto-send state is lost when Discord reconnects (WebSocket drops)

## Debug Tips

See `DEBUG_AUTO_SEND.md` for comprehensive debugging guide.

Quick checks:
```javascript
// Check plugin state
const plugin = BdApi.Plugins.get("StockAlertMonitor").instance
console.log("Sent messages:", plugin.sentMessageIds.size)
console.log("Auto-send:", plugin.autoSendEnabled)
console.log("Queue:", plugin.autoSendQueue.length)

// Check visible messages
const messages = plugin.getVisibleMessages()
console.log("Visible:", messages.length)
console.log("Unsent:", messages.filter(m => !plugin.sentMessageIds.has(m.id)).length)
```

