/**
 * @name StockAlertMonitor
 * @description Monitors Discord channels for stock alerts (TICKER < $X pattern) and provides backfill widget
 * @version 2.1.0
 * @author lpender
 */

// Get Dispatcher at module load time (like PingNotification does)
// This is more reliable than getting it inside methods
const { Webpack } = BdApi;
const Dispatcher = Webpack.getModule(Webpack.Filters.byKeys("subscribe", "dispatch"));

if (!Dispatcher) {
    console.error("[StockAlertMonitor] CRITICAL: Could not find Dispatcher module!");
} else {
    console.log("[StockAlertMonitor] Dispatcher found at module load time");
}

module.exports = class StockAlertMonitor {
    constructor() {
        this.alertSound = null;
        this.seenMessages = new Set();
        this.seenBackfill = new Set();
        this.widgetContainer = null;
        this.updateInterval = null;

        // Load saved settings or use defaults
        const savedSettings = BdApi.Data.load("StockAlertMonitor", "settings") || {};
        console.log("[StockAlertMonitor] Loaded settings:", JSON.stringify(savedSettings));

        // Channels enabled for live trading (by channel ID)
        this.enabledChannels = new Set(savedSettings.enabledChannels || []);
        this.alertWebhookUrl = savedSettings.alertWebhookUrl || "http://localhost:8765/alert";
        this.backfillWebhookUrl = savedSettings.backfillWebhookUrl || "http://localhost:8765/backfill";

        console.log("[StockAlertMonitor] Enabled channels:", Array.from(this.enabledChannels));
    }

    saveSettings() {
        const settings = {
            enabledChannels: Array.from(this.enabledChannels),
            alertWebhookUrl: this.alertWebhookUrl,
            backfillWebhookUrl: this.backfillWebhookUrl,
        };
        console.log("[StockAlertMonitor] Saving settings:", JSON.stringify(settings));
        BdApi.Data.save("StockAlertMonitor", "settings", settings);
    }

    getCurrentChannel() {
        // Get the currently selected channel
        const SelectedChannelStore = BdApi.Webpack.getModule(m => m.getChannelId && m.getVoiceChannelId);
        const ChannelStore = BdApi.Webpack.getModule(m => m.getChannel && m.getDMFromUserId);
        const channelId = SelectedChannelStore?.getChannelId();
        if (!channelId) return null;
        const channel = ChannelStore?.getChannel(channelId);
        return channel ? { id: channelId, name: channel.name || "unknown" } : null;
    }

    isChannelEnabled(channelId) {
        return this.enabledChannels.has(channelId);
    }

    toggleCurrentChannel() {
        const channel = this.getCurrentChannel();
        if (!channel) {
            BdApi.UI.showToast("No channel selected", { type: "error" });
            return;
        }

        if (this.enabledChannels.has(channel.id)) {
            this.enabledChannels.delete(channel.id);
            BdApi.UI.showToast(`Disabled live trading for #${channel.name}`, { type: "info" });
        } else {
            this.enabledChannels.add(channel.id);
            BdApi.UI.showToast(`ENABLED live trading for #${channel.name}`, { type: "success" });
        }
        this.saveSettings();
        this.updateChannelToggleButton();
        this.updateChannelIndicators();
    }

    updateChannelToggleButton() {
        const btn = document.getElementById("channel-toggle-btn");
        if (!btn) return;

        const channel = this.getCurrentChannel();
        if (!channel) {
            btn.textContent = "No channel selected";
            btn.className = "widget-btn disabled";
            return;
        }

        const isEnabled = this.isChannelEnabled(channel.id);
        btn.textContent = isEnabled ? `● #${channel.name} ENABLED` : `○ Enable #${channel.name}`;
        btn.className = isEnabled ? "widget-btn enabled" : "widget-btn";
        btn.style.background = isEnabled ? "#3ba55c" : "#5865f2";
    }

    // ============== Channel Indicators ==============

    startChannelIndicators() {
        // Inject CSS for indicators
        const css = `
            .sam-channel-indicator {
                color: #3ba55c;
                font-size: 10px;
                margin-left: 4px;
                vertical-align: middle;
            }
        `;
        BdApi.DOM.addStyle("StockAlertMonitor-indicators", css);

        // Initial update
        this.updateChannelIndicators();

        // Set up observer for DOM changes (channel list updates)
        this._channelIndicatorObserver = new MutationObserver(() => {
            this.updateChannelIndicators();
        });

        // Observe the channel list area
        const channelList = document.querySelector('[class*="sidebar"]');
        if (channelList) {
            this._channelIndicatorObserver.observe(channelList, {
                childList: true,
                subtree: true
            });
        }

        // Also update periodically (catches any missed updates)
        this._indicatorInterval = setInterval(() => this.updateChannelIndicators(), 3000);
    }

    stopChannelIndicators() {
        BdApi.DOM.removeStyle("StockAlertMonitor-indicators");
        if (this._channelIndicatorObserver) {
            this._channelIndicatorObserver.disconnect();
            this._channelIndicatorObserver = null;
        }
        if (this._indicatorInterval) {
            clearInterval(this._indicatorInterval);
            this._indicatorInterval = null;
        }
        // Remove all indicators
        document.querySelectorAll(".sam-channel-indicator").forEach(el => el.remove());
    }

    updateChannelIndicators() {
        // Find all channel links in the sidebar
        const channelLinks = document.querySelectorAll('[class*="link_"][class*="channel_"], [data-list-item-id^="channels___"]');

        channelLinks.forEach(link => {
            // Try to extract channel ID from the element
            const dataId = link.getAttribute("data-list-item-id");
            let channelId = null;

            if (dataId && dataId.startsWith("channels___")) {
                channelId = dataId.replace("channels___", "");
            } else {
                // Try href approach
                const href = link.getAttribute("href");
                if (href) {
                    const match = href.match(/\/channels\/\d+\/(\d+)/);
                    if (match) channelId = match[1];
                }
            }

            if (!channelId) return;

            // Find the channel name element
            const nameEl = link.querySelector('[class*="name_"]') || link.querySelector('[class*="channelName"]');
            if (!nameEl) return;

            // Check if indicator already exists
            let indicator = nameEl.querySelector(".sam-channel-indicator");
            const isEnabled = this.isChannelEnabled(channelId);

            if (isEnabled && !indicator) {
                // Add indicator
                indicator = document.createElement("span");
                indicator.className = "sam-channel-indicator";
                indicator.textContent = "📈";
                indicator.title = "Live trading enabled";
                nameEl.appendChild(indicator);
            } else if (!isEnabled && indicator) {
                // Remove indicator
                indicator.remove();
            }
        });
    }

    start() {
        console.log("[StockAlertMonitor] Starting...");

        // Create alert sound
        this.alertSound = new Audio("https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3");
        this.alertSound.volume = 0.7;

        // Subscribe to Discord's message dispatcher
        this.patchMessageCreate();

        // Create backfill widget after a short delay
        setTimeout(() => this.createBackfillWidget(), 2000);

        // Inject trade buttons on existing messages
        setTimeout(() => this.startTradeButtonInjection(), 1000);

        // Start channel indicators (shows emoji next to enabled channels)
        setTimeout(() => this.startChannelIndicators(), 1500);

        BdApi.UI.showToast("Stock Alert Monitor started!", { type: "success" });
    }

    stop() {
        console.log("[StockAlertMonitor] Stopping...");

        // Unsubscribe from MESSAGE_CREATE events using global Dispatcher
        if (Dispatcher && this.messageCreateHandler) {
            Dispatcher.unsubscribe("MESSAGE_CREATE", this.messageCreateHandler);
            console.log("[StockAlertMonitor] Unsubscribed from MESSAGE_CREATE events");
        }

        BdApi.Patcher.unpatchAll("StockAlertMonitor");
        this.removeBackfillWidget();
        this.stopTradeButtonInjection();
        this.stopChannelIndicators();
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }
        BdApi.UI.showToast("Stock Alert Monitor stopped", { type: "info" });
    }

    patchMessageCreate() {
        // Use the global Dispatcher (loaded at module init, like PingNotification)
        if (!Dispatcher) {
            console.error("[StockAlertMonitor] Dispatcher not available - cannot subscribe to MESSAGE_CREATE");
            BdApi.UI.showToast("StockAlertMonitor: Dispatcher not found!", { type: "error" });
            return;
        }

        // Define handler exactly like PingNotification does
        this.messageCreateHandler = (event) => {
            if (!event?.message) return;
            this.handleMessage(event);
        };

        // Subscribe to MESSAGE_CREATE
        Dispatcher.subscribe("MESSAGE_CREATE", this.messageCreateHandler);
        console.log("[StockAlertMonitor] Subscribed to MESSAGE_CREATE via global Dispatcher");
    }

    handleMessage(event) {
        // Debug: log that we received ANY message event
        console.log("[StockAlertMonitor] MESSAGE_CREATE event received");

        const { message, channelId } = event;

        if (!message) {
            console.log("[StockAlertMonitor] No message in event:", event);
            return;
        }
        if (!message.content) {
            console.log("[StockAlertMonitor] Message has no content:", message);
            return;
        }

        // Avoid duplicate processing
        if (this.seenMessages.has(message.id)) {
            console.log("[StockAlertMonitor] Duplicate message, skipping:", message.id);
            return;
        }
        this.seenMessages.add(message.id);

        // Limit seen messages set size
        if (this.seenMessages.size > 1000) {
            const arr = Array.from(this.seenMessages);
            this.seenMessages = new Set(arr.slice(-500));
        }

        // Get channel info
        const ChannelStore = BdApi.Webpack.getModule(m => m.getChannel && m.getDMFromUserId);
        const channel = ChannelStore?.getChannel(channelId);
        const channelName = channel?.name || "";

        // Debug: show message content for pattern debugging
        const contentPreview = message.content.substring(0, 100);
        console.log(`[StockAlertMonitor] Message in #${channelName}: "${contentPreview}"`);

        // Check for stock alert pattern: TICKER < $X (handles **TICKER** markdown bold)
        const tickerMatch = message.content.match(/\*{0,2}([A-Z]{2,5})\*{0,2}\s*<\s*\$[\d.]+/);

        if (tickerMatch) {
            const ticker = tickerMatch[1];
            const fullMatch = tickerMatch[0];
            const author = message?.author?.globalName || message?.author?.global_name || message?.author?.username || null;

            console.log(`[StockAlertMonitor] ALERT: ${ticker} in #${channelName} (channel enabled: ${this.isChannelEnabled(channelId)})`);

            this.triggerAlert(ticker, fullMatch, channelName, message.content, author, channelId);
        } else {
            console.log(`[StockAlertMonitor] No ticker pattern match in message`);
        }
    }

    triggerAlert(ticker, priceInfo, channelName, fullContent, author, channelId) {
        const timestamp = new Date().toLocaleTimeString();
        const isChannelEnabled = channelId && this.isChannelEnabled(channelId);

        // 1. Play sound (always, for any alert)
        if (this.alertSound) {
            this.alertSound.currentTime = 0;
            this.alertSound.play().catch(e => console.log("Audio play failed:", e));
        }

        // 2. Show BetterDiscord toast
        const toastSuffix = isChannelEnabled ? " → TRADING" : "";
        BdApi.UI.showToast(`NEW ALERT: ${priceInfo}${toastSuffix}`, {
            type: isChannelEnabled ? "success" : "warning",
            timeout: 10000
        });

        // 3. Browser notification (if permitted)
        if (Notification.permission === "granted") {
            new Notification(`Stock Alert: ${ticker}${isChannelEnabled ? " → TRADING" : ""}`, {
                body: `${priceInfo}\nChannel: #${channelName}`,
                icon: "https://cdn-icons-png.flaticon.com/512/2534/2534204.png",
                requireInteraction: true
            });
        } else if (Notification.permission !== "denied") {
            Notification.requestPermission();
        }

        // 4. Console log with full details
        console.log(`
========== STOCK ALERT ==========
Time: ${timestamp}
Ticker: ${ticker}
Info: ${priceInfo}
Channel: #${channelName}
Live Trading: ${isChannelEnabled ? "YES" : "NO"}
Full message: ${fullContent.substring(0, 200)}
=================================
        `);

        // 5. Send to local webhook only if channel is enabled for live trading
        if (isChannelEnabled) {
            this.sendToWebhook(this.alertWebhookUrl, {
                ticker: ticker,
                price_info: priceInfo,
                channel: channelName,
                content: fullContent,
                timestamp: new Date().toISOString(),
                author: author
            });
            console.log(`[StockAlertMonitor] Alert sent to trading server: ${ticker}`);
        } else {
            console.log(`[StockAlertMonitor] Alert NOT sent (channel not enabled): ${ticker}`);
        }
    }

    async sendToWebhook(url, data) {
        try {
            const response = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
            return response.ok;
        } catch (e) {
            // Webhook not running - that's OK
            return false;
        }
    }

    // ============== Trade Button Injection ==============

    startTradeButtonInjection() {
        // Inject CSS for trade buttons
        this.injectTradeButtonStyles();

        // Initial injection
        this.injectTradeButtons();

        // Set up mutation observer to catch new messages
        this._tradeButtonObserver = new MutationObserver((mutations) => {
            let shouldInject = false;
            for (const mutation of mutations) {
                if (mutation.addedNodes.length > 0) {
                    shouldInject = true;
                    break;
                }
            }
            if (shouldInject) {
                // Debounce
                if (this._tradeButtonTimeout) clearTimeout(this._tradeButtonTimeout);
                this._tradeButtonTimeout = setTimeout(() => this.injectTradeButtons(), 100);
            }
        });

        // Observe the message list
        const messageList = document.querySelector('[data-list-id="chat-messages"]') ||
                           document.querySelector('[class*="messagesWrapper"]');
        if (messageList) {
            this._tradeButtonObserver.observe(messageList, { childList: true, subtree: true });
        }

        // Also periodically check (handles channel switches)
        this._tradeButtonInterval = setInterval(() => this.injectTradeButtons(), 3000);

        console.log("[StockAlertMonitor] Trade button injection started");
    }

    stopTradeButtonInjection() {
        if (this._tradeButtonObserver) {
            this._tradeButtonObserver.disconnect();
            this._tradeButtonObserver = null;
        }
        if (this._tradeButtonInterval) {
            clearInterval(this._tradeButtonInterval);
            this._tradeButtonInterval = null;
        }
        if (this._tradeButtonTimeout) {
            clearTimeout(this._tradeButtonTimeout);
            this._tradeButtonTimeout = null;
        }
        // Remove all injected buttons
        document.querySelectorAll('.sam-trade-btn').forEach(btn => btn.remove());
        // Remove injected styles
        document.getElementById('sam-trade-btn-styles')?.remove();
    }

    injectTradeButtonStyles() {
        if (document.getElementById('sam-trade-btn-styles')) return;

        const style = document.createElement('style');
        style.id = 'sam-trade-btn-styles';
        style.textContent = `
            .sam-trade-btn {
                display: inline-flex;
                align-items: center;
                padding: 2px 8px;
                margin-left: 8px;
                background: #5865f2;
                color: white;
                font-size: 11px;
                font-weight: 600;
                border: none;
                border-radius: 3px;
                cursor: pointer;
                opacity: 0.8;
                transition: all 0.15s ease;
                vertical-align: middle;
            }
            .sam-trade-btn:hover {
                opacity: 1;
                background: #4752c4;
                transform: scale(1.05);
            }
            .sam-trade-btn:active {
                background: #3c45a5;
                transform: scale(0.98);
            }
            .sam-trade-btn.sent {
                background: #3ba55c;
                pointer-events: none;
            }
            .sam-trade-btn.failed {
                background: #ed4245;
            }
        `;
        document.head.appendChild(style);
    }

    injectTradeButtons() {
        const messageElements = document.querySelectorAll('li[id^="chat-messages-"]');

        messageElements.forEach((msgEl) => {
            // Skip if already has a trade button
            if (msgEl.querySelector('.sam-trade-btn')) return;

            // Get message content
            const contentEl = msgEl.querySelector('div[id^="message-content-"]');
            if (!contentEl) return;

            const content = contentEl.textContent?.trim() || "";

            // Check for stock alert pattern: TICKER < $X
            const tickerMatch = content.match(/\b([A-Z]{2,5})\s*<\s*\$[\d.]+/);
            if (!tickerMatch) return;

            const ticker = tickerMatch[1];
            const priceInfo = tickerMatch[0];

            // Create trade button
            const btn = document.createElement('button');
            btn.className = 'sam-trade-btn';
            btn.textContent = `▶ ${ticker}`;
            btn.title = `Send ${ticker} to trading server`;

            // Gather message data
            const timeEl = msgEl.querySelector("time[datetime]");
            const timestamp = timeEl?.getAttribute("datetime") || new Date().toISOString();

            // Get author - try multiple selectors
            const authorEl = msgEl.querySelector('[class*="username_"], [class*="headerText_"] [class*="username"], h3[class*="header_"] span');
            const author = authorEl?.textContent?.trim() || null;

            // Get full content with emoji alt text
            const clone = contentEl.cloneNode(true);
            clone.querySelectorAll('img.emoji').forEach(img => {
                const alt = img.alt || img.dataset?.name || '';
                img.replaceWith(alt);
            });
            const fullContent = clone.textContent?.trim() || content;

            // Handle click
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                btn.textContent = '...';
                btn.disabled = true;

                const channel = this.getCurrentChannelName();

                const success = await this.sendToWebhook(this.alertWebhookUrl, {
                    ticker: ticker,
                    price_info: priceInfo,
                    channel: channel,
                    content: fullContent,
                    timestamp: timestamp,
                    author: author,
                    manual: true  // Flag to indicate manual trigger
                });

                if (success) {
                    btn.textContent = `✓ ${ticker}`;
                    btn.className = 'sam-trade-btn sent';
                    BdApi.UI.showToast(`Sent ${ticker} to trading server`, { type: "success" });
                } else {
                    btn.textContent = `✗ ${ticker}`;
                    btn.className = 'sam-trade-btn failed';
                    btn.disabled = false;
                    BdApi.UI.showToast(`Failed to send ${ticker} - server running?`, { type: "error" });

                    // Reset after 2 seconds
                    setTimeout(() => {
                        btn.textContent = `▶ ${ticker}`;
                        btn.className = 'sam-trade-btn';
                    }, 2000);
                }
            });

            // Insert button after the message content
            contentEl.appendChild(btn);
        });
    }

    // ============== Backfill Widget ==============

    getCurrentChannelName() {
        const SelectedChannelStore = BdApi.Webpack.getModule(m => m.getChannelId && m.getVoiceChannelId);
        const ChannelStore = BdApi.Webpack.getModule(m => m.getChannel && m.getDMFromUserId);

        const channelId = SelectedChannelStore?.getChannelId();
        if (!channelId) return "unknown";

        const channel = ChannelStore?.getChannel(channelId);
        return channel?.name || "unknown";
    }

    getVisibleMessageTimestamps() {
        const timeElements = document.querySelectorAll("time[datetime]");
        if (timeElements.length === 0) {
            return { first: null, last: null, count: 0 };
        }

        const first = timeElements[0].getAttribute("datetime");
        const last = timeElements[timeElements.length - 1].getAttribute("datetime");

        return { first, last, count: timeElements.length };
    }

    formatTimestamp(isoString) {
        if (!isoString) return "N/A";
        try {
            const date = new Date(isoString);
            return date.toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false
            });
        } catch {
            return isoString;
        }
    }

    getVisibleMessages() {
        const messages = [];

        // Find all message list items
        const messageElements = document.querySelectorAll('li[id^="chat-messages-"]');

        // Track author across messages - Discord only shows author when it changes
        let lastAuthor = "";

        messageElements.forEach((msgEl) => {
            const id = msgEl.id.replace("chat-messages-", "");

            // Get timestamp
            const timeEl = msgEl.querySelector("time[datetime]");
            const timestamp = timeEl?.getAttribute("datetime") || "";

            // Get message content - clone to avoid modifying the DOM
            const contentEl = msgEl.querySelector('div[id^="message-content-"]');
            let content = "";
            if (contentEl) {
                // Clone the element so we can modify it
                const clone = contentEl.cloneNode(true);
                // Replace emoji <img> elements with their alt text (e.g., :flag_ca:)
                clone.querySelectorAll('img.emoji').forEach(img => {
                    const alt = img.alt || img.dataset?.name || '';
                    img.replaceWith(alt);
                });
                content = clone.textContent?.trim() || "";
            }

            if (content && timestamp) {
                // Best-effort author extraction from DOM
                // Try multiple selectors - Discord's class names can vary
                const authorEl = msgEl.querySelector('[class*="username_"], [class*="headerText_"] [class*="username"], h3[class*="header_"] span');
                const authorFromDom = authorEl?.textContent?.trim() || "";

                // Use author from DOM if present, otherwise use last seen author
                if (authorFromDom) {
                    lastAuthor = authorFromDom;
                }
                const author = lastAuthor;

                messages.push({ id, content, timestamp, author });
            }
        });

        return messages;
    }

    updateWidgetDisplay() {
        if (!this.widgetContainer) return;

        const timestamps = this.getVisibleMessageTimestamps();
        const channel = this.getCurrentChannelName();

        const firstSpan = this.widgetContainer.querySelector("#backfill-first");
        const lastSpan = this.widgetContainer.querySelector("#backfill-last");
        const countSpan = this.widgetContainer.querySelector("#backfill-count");
        const channelSpan = this.widgetContainer.querySelector("#backfill-channel");

        if (firstSpan) firstSpan.textContent = this.formatTimestamp(timestamps.first);
        if (lastSpan) lastSpan.textContent = this.formatTimestamp(timestamps.last);
        if (countSpan) countSpan.textContent = String(timestamps.count);
        if (channelSpan) channelSpan.textContent = `#${channel}`;
    }

    async handleSendData() {
        const statusEl = this.widgetContainer?.querySelector("#backfill-status");
        const channel = this.getCurrentChannelName();
        const messages = this.getVisibleMessages();

        if (messages.length === 0) {
            if (statusEl) {
                statusEl.textContent = "No messages found!";
                statusEl.style.color = "#f04747";
            }
            return;
        }

        if (statusEl) {
            statusEl.textContent = `Sending ${messages.length} messages...`;
            statusEl.style.color = "#faa61a";
        }

        const success = await this.sendToWebhook(this.backfillWebhookUrl, {
            channel,
            messages,
            sent_at: new Date().toISOString()
        });

        if (statusEl) {
            if (success) {
                statusEl.textContent = `Sent ${messages.length} messages!`;
                statusEl.style.color = "#43b581";
            } else {
                statusEl.textContent = "Failed - server not running?";
                statusEl.style.color = "#f04747";
            }
            // Clear status after 3 seconds
            setTimeout(() => {
                if (statusEl) statusEl.textContent = "";
            }, 3000);
        }
    }

    findChatScroller() {
        // Discord's chat scroller has specific class patterns
        // Try multiple selectors to find it
        const selectors = [
            '[class*="messagesWrapper"] [class*="scroller"]',
            '[class*="chatContent"] [class*="scroller"]',
            '[data-list-id="chat-messages"]',
            '[class*="scrollerInner"]'
        ];

        for (const selector of selectors) {
            const el = document.querySelector(selector);
            if (el) return el;
        }

        // Fallback: find by scrollable behavior
        const scrollers = document.querySelectorAll('[class*="scroller"]');
        for (const scroller of scrollers) {
            if (scroller.scrollHeight > scroller.clientHeight) {
                return scroller;
            }
        }

        return null;
    }

    createBackfillWidget() {
        // Remove existing widget if any
        this.removeBackfillWidget();

        this.widgetContainer = document.createElement("div");
        this.widgetContainer.id = "stock-alert-backfill-widget";
        this.widgetContainer.innerHTML = `
            <style>
                #stock-alert-backfill-widget {
                    position: fixed;
                    bottom: 20px;
                    right: 20px;
                    background: #2f3136;
                    border: 1px solid #202225;
                    border-radius: 8px;
                    padding: 12px 16px;
                    font-family: "gg sans", "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
                    font-size: 13px;
                    color: #dcddde;
                    z-index: 9999;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                    min-width: 220px;
                }
                #stock-alert-backfill-widget .widget-header {
                    font-weight: 600;
                    color: #fff;
                    margin-bottom: 8px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }
                #stock-alert-backfill-widget .widget-close {
                    cursor: pointer;
                    color: #72767d;
                    font-size: 16px;
                }
                #stock-alert-backfill-widget .widget-close:hover {
                    color: #dcddde;
                }
                #stock-alert-backfill-widget .widget-row {
                    display: flex;
                    justify-content: space-between;
                    margin: 4px 0;
                }
                #stock-alert-backfill-widget .widget-label {
                    color: #8e9297;
                }
                #stock-alert-backfill-widget .widget-value {
                    color: #fff;
                    font-weight: 500;
                }
                #stock-alert-backfill-widget .widget-channel {
                    color: #7289da;
                }
                #stock-alert-backfill-widget .widget-btn {
                    width: 100%;
                    margin-top: 10px;
                    padding: 8px 16px;
                    background: #5865f2;
                    color: #fff;
                    border: none;
                    border-radius: 4px;
                    font-size: 14px;
                    font-weight: 500;
                    cursor: pointer;
                    transition: background 0.2s;
                }
                #stock-alert-backfill-widget .widget-btn:hover {
                    background: #4752c4;
                }
                #stock-alert-backfill-widget .widget-btn:active {
                    background: #3c45a5;
                }
                #stock-alert-backfill-widget .widget-status {
                    margin-top: 8px;
                    text-align: center;
                    font-size: 12px;
                    min-height: 16px;
                }
                #stock-alert-backfill-widget .widget-toggle {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    padding: 8px 10px;
                    margin-bottom: 8px;
                    border-radius: 4px;
                    cursor: pointer;
                    transition: background 0.2s;
                }
                #stock-alert-backfill-widget .widget-toggle.active {
                    background: #3ba55c;
                }
                #stock-alert-backfill-widget .widget-toggle.inactive {
                    background: #40444b;
                }
                #stock-alert-backfill-widget .widget-toggle:hover {
                    filter: brightness(1.1);
                }
                #stock-alert-backfill-widget .widget-toggle-label {
                    font-size: 13px;
                    font-weight: 500;
                }
                #stock-alert-backfill-widget .widget-toggle-dot {
                    width: 12px;
                    height: 12px;
                    border-radius: 50%;
                    background: #fff;
                }
            </style>
            <div class="widget-header">
                <span>Stock Alert Monitor</span>
                <span class="widget-close" id="backfill-close">&times;</span>
            </div>
            <button class="widget-btn" id="channel-toggle-btn" style="margin-bottom: 8px;">
                Loading...
            </button>
            <div class="widget-row">
                <span class="widget-label">Channel:</span>
                <span class="widget-value widget-channel" id="backfill-channel">#unknown</span>
            </div>
            <div class="widget-row">
                <span class="widget-label">First:</span>
                <span class="widget-value" id="backfill-first">N/A</span>
            </div>
            <div class="widget-row">
                <span class="widget-label">Last:</span>
                <span class="widget-value" id="backfill-last">N/A</span>
            </div>
            <div class="widget-row">
                <span class="widget-label">Messages:</span>
                <span class="widget-value" id="backfill-count">0</span>
            </div>
            <button class="widget-btn" id="backfill-send">Send Data</button>
            <div class="widget-status" id="backfill-status"></div>
        `;

        document.body.appendChild(this.widgetContainer);

        // Add event listeners
        const closeBtn = this.widgetContainer.querySelector("#backfill-close");
        closeBtn?.addEventListener("click", () => {
            if (this.widgetContainer) this.widgetContainer.style.display = "none";
        });

        const sendBtn = this.widgetContainer.querySelector("#backfill-send");
        sendBtn?.addEventListener("click", () => this.handleSendData());

        // Channel toggle button
        const channelToggleBtn = this.widgetContainer.querySelector("#channel-toggle-btn");
        channelToggleBtn?.addEventListener("click", () => this.toggleCurrentChannel());

        // Update button when channel changes
        this.updateChannelToggleButton();

        // Listen for channel changes via Discord's dispatcher
        const Dispatcher = BdApi.Webpack.getModule(m => m.dispatch && m.subscribe);
        if (Dispatcher) {
            this._channelChangeHandler = () => {
                setTimeout(() => this.updateChannelToggleButton(), 100);
            };
            Dispatcher.subscribe("CHANNEL_SELECT", this._channelChangeHandler);
        }

        // Set up scroll listener - debounced
        let scrollTimeout = null;
        const debouncedScroll = () => {
            if (scrollTimeout) clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => this.updateWidgetDisplay(), 100);
        };

        // Find and attach to Discord's chat scroller
        this._scrollHandler = debouncedScroll;
        this._attachedScroller = null;

        const attachScrollListener = () => {
            const chatScroller = this.findChatScroller();
            if (chatScroller && chatScroller !== this._attachedScroller) {
                // Remove from old scroller if any
                if (this._attachedScroller) {
                    this._attachedScroller.removeEventListener("scroll", this._scrollHandler);
                }
                // Attach to new scroller
                chatScroller.addEventListener("scroll", this._scrollHandler);
                this._attachedScroller = chatScroller;
                console.log("[StockAlertMonitor] Attached scroll listener to chat scroller");
            }
        };

        // Also listen at document level as backup (capture phase)
        document.addEventListener("scroll", debouncedScroll, true);

        // Listen for wheel events as backup (virtual scrollers often use this)
        document.addEventListener("wheel", debouncedScroll, true);

        // Initial attachment
        attachScrollListener();

        // Initial update
        this.updateWidgetDisplay();

        // Update periodically and re-attach scroll listener if needed (e.g., channel change)
        this.updateInterval = setInterval(() => {
            this.updateWidgetDisplay();
            attachScrollListener();
        }, 2000);

        console.log("[StockAlertMonitor] Backfill widget created");
    }

    removeBackfillWidget() {
        // Unsubscribe from channel changes
        if (this._channelChangeHandler) {
            const Dispatcher = BdApi.Webpack.getModule(m => m.dispatch && m.subscribe);
            Dispatcher?.unsubscribe("CHANNEL_SELECT", this._channelChangeHandler);
            this._channelChangeHandler = null;
        }

        if (this.widgetContainer) {
            this.widgetContainer.remove();
            this.widgetContainer = null;
        }
    }

    getSettingsPanel() {
        const panel = document.createElement("div");
        panel.style.padding = "10px";

        const enabledCount = this.enabledChannels.size;

        panel.innerHTML = `
            <h3 style="color: white; margin-bottom: 10px;">Stock Alert Monitor Settings</h3>

            <div style="margin-bottom: 15px; padding: 10px; background: #40444b; border-radius: 4px;">
                <div style="color: #b9bbbe; font-size: 12px; margin-bottom: 8px;">
                    <strong style="color: white;">How it works:</strong> Use the widget button to enable/disable
                    live trading for each channel. Navigate to a channel and click the toggle button.
                </div>
                <div style="color: ${enabledCount > 0 ? '#3ba55c' : '#ed4245'}; font-weight: bold;">
                    ${enabledCount} channel${enabledCount !== 1 ? 's' : ''} enabled for live trading
                </div>
            </div>

            <div style="margin-bottom: 10px;">
                <label style="color: #b9bbbe;">Alert webhook URL:</label><br>
                <input type="text" id="sam-alert-webhook" value="${this.alertWebhookUrl}"
                    style="width: 100%; padding: 8px; margin-top: 5px; background: #40444b; border: none; border-radius: 4px; color: white;">
            </div>
            <div style="margin-bottom: 10px;">
                <label style="color: #b9bbbe;">Backfill webhook URL:</label><br>
                <input type="text" id="sam-backfill-webhook" value="${this.backfillWebhookUrl}"
                    style="width: 100%; padding: 8px; margin-top: 5px; background: #40444b; border: none; border-radius: 4px; color: white;">
            </div>
            <button id="sam-save" style="background: #5865f2; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer;">
                Save URLs
            </button>
            <button id="sam-clear" style="background: #ed4245; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-left: 10px;">
                Clear All Channels
            </button>
            <button id="sam-toggle-widget" style="background: #faa61a; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-left: 10px;">
                Toggle Widget
            </button>
        `;

        // Add event listeners after panel is created
        setTimeout(() => {
            document.getElementById("sam-save")?.addEventListener("click", () => {
                this.alertWebhookUrl = document.getElementById("sam-alert-webhook").value;
                this.backfillWebhookUrl = document.getElementById("sam-backfill-webhook").value;
                this.saveSettings();
                BdApi.UI.showToast("Settings saved!", { type: "success" });
            });

            document.getElementById("sam-clear")?.addEventListener("click", () => {
                this.enabledChannels.clear();
                this.saveSettings();
                this.updateChannelToggleButton();
                this.updateChannelIndicators();
                BdApi.UI.showToast("All channels disabled", { type: "info" });
            });

            document.getElementById("sam-toggle-widget")?.addEventListener("click", () => {
                if (this.widgetContainer) {
                    const isHidden = this.widgetContainer.style.display === "none";
                    this.widgetContainer.style.display = isHidden ? "block" : "none";
                } else {
                    this.createBackfillWidget();
                }
            });
        }, 100);

        return panel;
    }
};
