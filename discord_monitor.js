// Discord Message Monitor
// Paste this into Discord's browser console (F12 > Console)
// Make sure discord_server.py is running first!

(function() {
    // Use 127.0.0.1 instead of localhost to bypass Discord's CSP
    const SERVER_URL = 'http://127.0.0.1:8765';
    const CHECK_INTERVAL_MS = 500; // Check every 500ms

    let lastMessageId = null;
    let isRunning = true;

    console.log('%c[Discord Monitor] Starting...', 'color: #00ff00; font-weight: bold');

    function getLatestMessage() {
        // Find the messages container
        const messagesContainer = document.querySelector('[class*="messagesWrapper"]');
        if (!messagesContainer) {
            return null;
        }

        // Get all message groups
        const messageGroups = messagesContainer.querySelectorAll('[class*="messageListItem"]');
        if (messageGroups.length === 0) {
            return null;
        }

        // Get the last message group
        const lastGroup = messageGroups[messageGroups.length - 1];

        // Get message ID from data attribute
        const messageId = lastGroup.id;

        // Get message content
        const contentEl = lastGroup.querySelector('[class*="messageContent"]');
        const content = contentEl ? contentEl.textContent : '';

        // Get timestamp
        const timeEl = lastGroup.querySelector('time');
        const timestamp = timeEl ? timeEl.getAttribute('datetime') : new Date().toISOString();

        return {
            id: messageId,
            content: content,
            timestamp: timestamp
        };
    }

    async function sendToServer(message) {
        try {
            const response = await fetch(`${SERVER_URL}/message`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    message: message.content,
                    timestamp: message.timestamp,
                    id: message.id
                })
            });

            const result = await response.json();

            if (result.ticker) {
                console.log(`%c[Discord Monitor] ALERT: ${result.ticker}`, 'color: #ff0000; font-weight: bold; font-size: 14px');
                console.log(`%c  Action: ${result.action}`, 'color: #ffff00');
            }

            return result;
        } catch (error) {
            console.error('[Discord Monitor] Failed to send to server:', error.message);
            return null;
        }
    }

    function checkForNewMessages() {
        if (!isRunning) return;

        const message = getLatestMessage();

        if (message && message.id !== lastMessageId) {
            lastMessageId = message.id;

            // Only send if it looks like a ticker alert (has < $ pattern)
            if (message.content.match(/[A-Z]{2,5}\s+<\s*\$/)) {
                console.log('%c[Discord Monitor] New alert detected!', 'color: #00ff00');
                sendToServer(message);
            }
        }

        setTimeout(checkForNewMessages, CHECK_INTERVAL_MS);
    }

    // Start monitoring
    checkForNewMessages();

    // Store stop function globally
    window.stopDiscordMonitor = function() {
        isRunning = false;
        console.log('%c[Discord Monitor] Stopped', 'color: #ff0000; font-weight: bold');
    };

    console.log('%c[Discord Monitor] Running! Use stopDiscordMonitor() to stop.', 'color: #00ff00; font-weight: bold');
    console.log('%c[Discord Monitor] Watching for ticker alerts...', 'color: #888888');
})();
