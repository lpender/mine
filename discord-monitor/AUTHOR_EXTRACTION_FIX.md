# Author Extraction Fix - v2.3.0

## Problem

Messages from author "PR - Spike" in the "select-news" channel were not being properly captured or processed. When examining recent backfill data, all messages showed empty author strings (`"author": ""`).

### Root Cause

The Discord plugin's DOM selector for extracting author names wasn't working:

```javascript
// Old selector - not working
const authorEl = msgEl.querySelector('[class*="username_"], [class*="headerText_"] [class*="username"], h3[class*="header_"] span');
```

This caused all messages to have empty authors. The Python backend would then try to infer the author based on channel name:
- "select-news" channel → "Nuntiobot"
- "pr-spike" channel → "PR - Spike"

**But this is wrong!** Messages from "PR - Spike" can appear in the "select-news" channel, as the user reported.

## Solution

### 1. Improved DOM Extraction

Implemented a multi-strategy approach for extracting author names from Discord's DOM:

```javascript
// Strategy 1: Look for header element with author info
// Discord typically shows: <h3><span class="header*"><span class="username*">AuthorName</span>...
const headerEl = msgEl.querySelector('h3[class*="header"]');
if (headerEl) {
    const usernameSpan = headerEl.querySelector('span[class*="username"]');
    if (usernameSpan) {
        authorFromDom = usernameSpan.textContent?.trim() || "";
    }
}

// Strategy 2: Direct username class search (fallback)
if (!authorFromDom) {
    const usernameEl = msgEl.querySelector('[class*="username_"]');
    if (usernameEl) {
        authorFromDom = usernameEl.textContent?.trim() || "";
    }
}

// Strategy 3: Look in headerText container (another fallback)
if (!authorFromDom) {
    const headerTextEl = msgEl.querySelector('[class*="headerText"]');
    if (headerTextEl) {
        const usernameEl = headerTextEl.querySelector('[class*="username"]');
        if (usernameEl) {
            authorFromDom = usernameEl.textContent?.trim() || "";
        }
    }
}
```

### 2. Enhanced Debug Logging

Added comprehensive logging to help diagnose author extraction issues:

- Log author changes as messages are parsed
- Log first 3 messages with author info
- Log total message count and unique author count
- Log author distribution when sending backfill

Example console output:
```
[StockAlertMonitor] Author change: "" -> "Nuntiobot"
[StockAlertMonitor] Message 1: author="Nuntiobot", content="AI  < $16  - US Army: C3 AI Selected to Deliver..."
[StockAlertMonitor] Author change: "Nuntiobot" -> "PR - Spike"
[StockAlertMonitor] Message 15: author="PR - Spike", content="HUIZ  < $5  - Huize Holding Limited (NASDAQ..."
[StockAlertMonitor] Extracted 50 messages, unique authors: 3
[StockAlertMonitor] Sending backfill - 50 messages from channel: select-news
[StockAlertMonitor] Author distribution: { "Nuntiobot": 35, "PR - Spike": 12, "MarketWatch": 3 }
```

### 3. Version Bump

Updated plugin version from 2.2.0 to 2.3.0 to track this fix.

## How to Update

### Option 1: Use Task Command (Recommended)

```bash
task plugin:install
```

### Option 2: Manual Update

```bash
cp /Users/lpender/dev/trading/backtest/discord-monitor/StockAlertMonitor.plugin.js \
   ~/Library/Application\ Support/BetterDiscord/plugins/
```

Then:
1. Open Discord
2. Go to User Settings > Plugins
3. Toggle StockAlertMonitor OFF then ON
4. Or restart Discord

## Testing

After updating:

1. **Open Discord DevTools**: `Cmd+Option+I` (Mac) or `Ctrl+Shift+I` (Windows)

2. **Navigate to the select-news channel**

3. **Look for debug messages in Console**:
   - Author changes should be logged
   - Message parsing should show author names
   - Check for "Author distribution" when sending backfill

4. **Send a backfill** by clicking "Send Data" in the widget

5. **Check the raw_messages archive**:
   ```bash
   # Look at the most recent backfill file
   ls -lt /Users/lpender/dev/trading/backtest/data/raw_messages/ | head -5

   # Examine author fields
   jq '.messages[0:5] | .[] | {author, content: .content[0:80]}' \
      /Users/lpender/dev/trading/backtest/data/raw_messages/backfill_YYYYMMDD_HHMMSS.json
   ```

6. **Verify messages from "PR - Spike"** are now being captured with the correct author

## Expected Behavior After Fix

- Messages from "PR - Spike" in any channel should show `"author": "PR - Spike"`
- Messages from "Nuntiobot" should show `"author": "Nuntiobot"`
- The channel-based inference should only be used as a **fallback** when DOM extraction fails
- All backfill data should have populated author fields (not empty strings)

## Troubleshooting

### If authors are still empty:

1. **Check Discord version** - Discord may have changed their DOM structure again
2. **Inspect the DOM manually**:
   ```javascript
   // In Discord DevTools console:
   // Find a message element and inspect its structure
   const msgEl = document.querySelector('li[id^="chat-messages-"]');
   console.log(msgEl);

   // Look for username elements
   console.log(msgEl.querySelectorAll('[class*="username"]'));
   console.log(msgEl.querySelectorAll('h3[class*="header"]'));
   ```

3. **Update selectors** if Discord changed their class names

### If console logs aren't showing:

- Make sure the plugin is enabled
- Disable and re-enable the plugin
- Check for JavaScript errors in console
- Verify plugin version: should show 2.3.0

## Additional Notes

- Discord only shows the author header when the author changes (to save space)
- The plugin tracks `lastAuthor` across messages to handle this
- Empty author strings will still fall back to channel-based inference in the Python backend
- This fix makes the extraction more robust but may need updates if Discord changes their UI again

