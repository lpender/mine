# Debug Auto-Send Not Working

## Check 1: Plugin is Running

In Discord DevTools Console (`Cmd+Option+I`):

```javascript
// Check if plugin is loaded
BdApi.Plugins.get("StockAlertMonitor")

// Check auto-send state
const plugin = BdApi.Plugins.get("StockAlertMonitor").instance
console.log("Auto-send enabled:", plugin.autoSendEnabled)
console.log("Queue length:", plugin.autoSendQueue.length)
console.log("Sent messages count:", plugin.sentMessageIds.size)
```

## Check 2: Discord Console Logs

Look for these messages in Discord console:
- `[StockAlertMonitor] Starting auto-send mode` - When you enable it
- `[StockAlertMonitor] Auto-send observer attached to message list` - Observer is watching
- `[StockAlertMonitor] Queued X new messages` - Messages detected
- `[StockAlertMonitor] Auto-sending batch of X messages` - Sending to server
- `[StockAlertMonitor] Auto-sent X messages successfully` - Server confirmed

## Check 3: Server is Running

```bash
# Check if server is listening on port 8765
lsof -i :8765
```

Should show:
```
COMMAND   PID    USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
Python   12345  user   5u   IPv4 0x...      0t0  TCP *:8765 (LISTEN)
```

## Check 4: Server Logs

```bash
# Watch server logs in real-time
tail -f /Users/lpender/dev/trading/backtest/logs/dev.log

# Or use task command
task logs
```

Look for:
```
[src.alert_service] [HTTP POST] Received POST request to /backfill
[src.alert_service] Backfill from #🍒-select-news: 150 messages
```

## Check 5: Network Request

In Discord DevTools Console, manually test the webhook:

```javascript
fetch('http://localhost:8765/backfill', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        channel: 'test',
        messages: [{
            id: 'test-123',
            content: 'TEST < $5',
            timestamp: new Date().toISOString(),
            author: 'Test'
        }],
        sent_at: new Date().toISOString()
    })
})
.then(r => r.json())
.then(d => console.log('Server response:', d))
.catch(e => console.error('Request failed:', e))
```

Should see:
- In Discord console: `Server response: {parsed: 1, new: 1, skipped: 0}`
- In server logs: `Backfill from #test: 1 messages`

## Check 6: All Messages Already Sent?

If auto-send is working but you don't see new activity, it might be because all visible messages were already sent.

To verify:
```javascript
const plugin = BdApi.Plugins.get("StockAlertMonitor").instance
const visible = plugin.getVisibleMessages()
console.log("Visible messages:", visible.length)

// Check how many are already sent
const unsent = visible.filter(m => !plugin.sentMessageIds.has(m.id))
console.log("Unsent messages:", unsent.length)
```

## Check 7: Clear Sent History (Re-process Messages)

If you want to re-send all messages:

```javascript
const plugin = BdApi.Plugins.get("StockAlertMonitor").instance
plugin.sentMessageIds.clear()
plugin.saveSentMessageIds()
console.log("Cleared sent history. Scroll to trigger auto-send.")
```

Then scroll the channel to see messages get auto-sent.

## Common Issues

### Issue: No logs in Discord console

**Solution**: Make sure you're looking at the right console:
1. Discord must be the **desktop app** (not browser)
2. DevTools must be for the **main Discord window** (not plugin settings)
3. Try filtering console by `StockAlertMonitor`

### Issue: "Auto-send failed, re-queued" messages

**Cause**: Server not reachable at `http://localhost:8765/backfill`

**Solutions**:
- Check server is running: `lsof -i :8765`
- Check webhook URL in plugin settings
- Check firewall/antivirus blocking localhost connections

### Issue: Messages sent but "Parsed: 0 | Skipped: X"

**Cause**: All messages already exist in database (already processed before)

**Explanation**: This is normal! The server deduplicates based on `(ticker, timestamp)`. If you already backfilled these messages, they won't be added again.

### Issue: Auto-send was working, now stopped

**Causes**:
1. **Discord reconnected** - WebSocket dropped and plugin state was lost
2. **Channel changed** - Auto-send only works in the channel where it was enabled
3. **Plugin disabled/reloaded** - State was reset

**Solution**: Toggle auto-send OFF then ON again

### Issue: getVisibleMessages() returning 0

**Cause**: DOM selectors broke (Discord updated their UI)

**Debug**:
```javascript
// Check if message elements are found
document.querySelectorAll('li[id^="chat-messages-"]').length
```

If 0, Discord changed their DOM structure and selectors need updating.

