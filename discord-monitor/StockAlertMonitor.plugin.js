/**
 * @name StockAlertMonitor
 * @description Monitors Discord channels for stock alerts (TICKER < $X pattern) and provides backfill widget
 * @version 2.0.0
 * @author lpender
 */

module.exports = class StockAlertMonitor {
    constructor() {
        this.alertSound = null;
        this.seenMessages = new Set();
        this.seenBackfill = new Set();
        this.channelFilter = ["pr-spike", "select-news"]; // Channels to monitor
        this.alertWebhookUrl = "http://localhost:8765/alert";
        this.backfillWebhookUrl = "http://localhost:8765/backfill";
        this.widgetContainer = null;
        this.updateInterval = null;
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

        BdApi.UI.showToast("Stock Alert Monitor started!", { type: "success" });
    }

    stop() {
        console.log("[StockAlertMonitor] Stopping...");
        BdApi.Patcher.unpatchAll("StockAlertMonitor");
        this.removeBackfillWidget();
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }
        BdApi.UI.showToast("Stock Alert Monitor stopped", { type: "info" });
    }

    patchMessageCreate() {
        const Dispatcher = BdApi.Webpack.getModule(m => m.dispatch && m.subscribe);

        if (!Dispatcher) {
            console.error("[StockAlertMonitor] Could not find Dispatcher");
            return;
        }

        // Subscribe to MESSAGE_CREATE events
        Dispatcher.subscribe("MESSAGE_CREATE", this.handleMessage.bind(this));

        // Store for cleanup
        this._unsubscribe = () => {
            Dispatcher.unsubscribe("MESSAGE_CREATE", this.handleMessage.bind(this));
        };
    }

    handleMessage(event) {
        const { message, channelId } = event;

        if (!message || !message.content) return;

        // Avoid duplicate processing
        if (this.seenMessages.has(message.id)) return;
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

        // Filter by channel name (if filter is set)
        if (this.channelFilter.length > 0) {
            const matchesFilter = this.channelFilter.some(name =>
                channelName.toLowerCase().includes(name.toLowerCase())
            );
            if (!matchesFilter) return;
        }

        // Check for stock alert pattern: TICKER < $X
        const tickerMatch = message.content.match(/\b([A-Z]{2,5})\s*<\s*\$[\d.]+/);

        if (tickerMatch) {
            const ticker = tickerMatch[1];
            const fullMatch = tickerMatch[0];
            const author = message?.author?.globalName || message?.author?.global_name || message?.author?.username || null;

            console.log(`[StockAlertMonitor] ALERT: ${ticker} in #${channelName}`);

            this.triggerAlert(ticker, fullMatch, channelName, message.content, author);
        }
    }

    triggerAlert(ticker, priceInfo, channelName, fullContent, author) {
        const timestamp = new Date().toLocaleTimeString();

        // 1. Play sound
        if (this.alertSound) {
            this.alertSound.currentTime = 0;
            this.alertSound.play().catch(e => console.log("Audio play failed:", e));
        }

        // 2. Show BetterDiscord toast
        BdApi.UI.showToast(`NEW ALERT: ${priceInfo}`, {
            type: "warning",
            timeout: 10000
        });

        // 3. Browser notification (if permitted)
        if (Notification.permission === "granted") {
            new Notification(`Stock Alert: ${ticker}`, {
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
Full message: ${fullContent.substring(0, 200)}
=================================
        `);

        // 5. Send to local webhook (for your trading system)
        this.sendToWebhook(this.alertWebhookUrl, {
            ticker: ticker,
            price_info: priceInfo,
            channel: channelName,
            content: fullContent,
            timestamp: new Date().toISOString(),
            author: author
        });
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
            </style>
            <div class="widget-header">
                <span>Backfill Widget</span>
                <span class="widget-close" id="backfill-close">&times;</span>
            </div>
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
        if (this.widgetContainer) {
            this.widgetContainer.remove();
            this.widgetContainer = null;
        }
    }

    getSettingsPanel() {
        const panel = document.createElement("div");
        panel.style.padding = "10px";

        panel.innerHTML = `
            <h3 style="color: white; margin-bottom: 10px;">Stock Alert Monitor Settings</h3>
            <div style="margin-bottom: 10px;">
                <label style="color: #b9bbbe;">Channels to monitor (comma-separated):</label><br>
                <input type="text" id="sam-channels" value="${this.channelFilter.join(", ")}"
                    style="width: 100%; padding: 8px; margin-top: 5px; background: #40444b; border: none; border-radius: 4px; color: white;">
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
                Save Settings
            </button>
            <button id="sam-test" style="background: #3ba55c; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-left: 10px;">
                Test Alert
            </button>
            <button id="sam-toggle-widget" style="background: #faa61a; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-left: 10px;">
                Toggle Widget
            </button>
        `;

        // Add event listeners after panel is created
        setTimeout(() => {
            document.getElementById("sam-save")?.addEventListener("click", () => {
                const channels = document.getElementById("sam-channels").value;
                this.channelFilter = channels.split(",").map(c => c.trim()).filter(c => c);
                this.alertWebhookUrl = document.getElementById("sam-alert-webhook").value;
                this.backfillWebhookUrl = document.getElementById("sam-backfill-webhook").value;
                BdApi.UI.showToast("Settings saved!", { type: "success" });
            });

            document.getElementById("sam-test")?.addEventListener("click", () => {
                this.triggerAlert("TEST", "TEST < $5.00", "test-channel", "Test alert message");
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
