/**
 * @name StockAlertMonitor
 * @description Monitors Discord channels for stock alerts (TICKER < $X pattern) and triggers notifications
 * @version 1.0.0
 * @author lpender
 */

module.exports = class StockAlertMonitor {
    constructor() {
        this.alertSound = null;
        this.seenMessages = new Set();
        this.channelFilter = ["pr-spike", "select-news"]; // Channels to monitor
        this.webhookUrl = "http://localhost:8765/alert"; // Local webhook (optional)
    }

    start() {
        console.log("[StockAlertMonitor] Starting...");

        // Create alert sound
        this.alertSound = new Audio("https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3");
        this.alertSound.volume = 0.7;

        // Subscribe to Discord's message dispatcher
        this.patchMessageCreate();

        BdApi.UI.showToast("Stock Alert Monitor started!", { type: "success" });
    }

    stop() {
        console.log("[StockAlertMonitor] Stopping...");
        BdApi.Patcher.unpatchAll("StockAlertMonitor");
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
            const ticker = tickerMatch[0];
            const fullMatch = tickerMatch[0];

            console.log(`[StockAlertMonitor] ALERT: ${ticker} in #${channelName}`);

            this.triggerAlert(ticker, fullMatch, channelName, message.content);
        }
    }

    triggerAlert(ticker, priceInfo, channelName, fullContent) {
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
        this.sendToWebhook({
            ticker: ticker,
            price_info: priceInfo,
            channel: channelName,
            content: fullContent,
            timestamp: new Date().toISOString()
        });
    }

    async sendToWebhook(data) {
        try {
            await fetch(this.webhookUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
        } catch (e) {
            // Webhook not running - that's OK
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
                <label style="color: #b9bbbe;">Local webhook URL:</label><br>
                <input type="text" id="sam-webhook" value="${this.webhookUrl}"
                    style="width: 100%; padding: 8px; margin-top: 5px; background: #40444b; border: none; border-radius: 4px; color: white;">
            </div>
            <button id="sam-save" style="background: #5865f2; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer;">
                Save Settings
            </button>
            <button id="sam-test" style="background: #3ba55c; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-left: 10px;">
                Test Alert
            </button>
        `;

        // Add event listeners after panel is created
        setTimeout(() => {
            document.getElementById("sam-save")?.addEventListener("click", () => {
                const channels = document.getElementById("sam-channels").value;
                this.channelFilter = channels.split(",").map(c => c.trim()).filter(c => c);
                this.webhookUrl = document.getElementById("sam-webhook").value;
                BdApi.UI.showToast("Settings saved!", { type: "success" });
            });

            document.getElementById("sam-test")?.addEventListener("click", () => {
                this.triggerAlert("TEST", "TEST < $5.00", "test-channel", "Test alert message");
            });
        }, 100);

        return panel;
    }
};
