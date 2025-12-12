/*
 * Vencord plugin: Stock Alert Monitor
 * Monitors Discord channels for stock alerts (TICKER < $X pattern)
 * Also provides a backfill widget to capture historical messages
 *
 * Installation:
 * 1. Enable "Vencord Desktop" in Vencord settings
 * 2. Copy this file to: ~/.config/Vencord/plugins/stockAlertMonitor.ts
 *    (or on macOS: ~/Library/Application Support/Vencord/plugins/)
 * 3. Restart Discord and enable the plugin in Vencord settings
 */

import { Devs } from "@utils/constants";
import definePlugin from "@utils/types";
import { FluxDispatcher, ChannelStore, SelectedChannelStore } from "@webpack/common";

const CHANNEL_FILTER = ["pr-spike", "select-news"]; // Channels to monitor for alerts
const ALERT_WEBHOOK_URL = "http://localhost:8765/alert";
const BACKFILL_WEBHOOK_URL = "http://localhost:8765/backfill";

// Track seen messages to avoid duplicates
const seenMessages = new Set<string>();

// Alert sound
let alertSound: HTMLAudioElement | null = null;

// Backfill widget elements
let widgetContainer: HTMLDivElement | null = null;
let scrollHandler: (() => void) | null = null;

function playAlertSound() {
    if (!alertSound) {
        alertSound = new Audio("https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3");
        alertSound.volume = 0.7;
    }
    alertSound.currentTime = 0;
    alertSound.play().catch(() => {});
}

function showNotification(ticker: string, priceInfo: string, channelName: string) {
    if (Notification.permission === "granted") {
        new Notification(`Stock Alert: ${ticker}`, {
            body: `${priceInfo}\nChannel: #${channelName}`,
            icon: "https://cdn-icons-png.flaticon.com/512/2534/2534204.png",
            requireInteraction: true
        });
    } else if (Notification.permission !== "denied") {
        Notification.requestPermission();
    }
}

async function sendToWebhook(url: string, data: object) {
    try {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
        return response.ok;
    } catch {
        return false;
    }
}

function handleMessage(event: any) {
    const { message, channelId } = event;

    if (!message?.content) return;

    // Dedupe
    if (seenMessages.has(message.id)) return;
    seenMessages.add(message.id);

    // Limit set size
    if (seenMessages.size > 1000) {
        const arr = Array.from(seenMessages);
        seenMessages.clear();
        arr.slice(-500).forEach(id => seenMessages.add(id));
    }

    // Get channel name
    const channel = ChannelStore.getChannel(channelId);
    const channelName = channel?.name || "";

    // Filter by channel
    if (CHANNEL_FILTER.length > 0) {
        const matches = CHANNEL_FILTER.some(name =>
            channelName.toLowerCase().includes(name.toLowerCase())
        );
        if (!matches) return;
    }

    // Check for stock alert pattern
    const tickerMatch = message.content.match(/\b([A-Z]{2,5})\s*<\s*\$[\d.]+/);

    if (tickerMatch) {
        const ticker = tickerMatch[1];
        const fullMatch = tickerMatch[0];
        const timestamp = new Date().toISOString();

        console.log(`[StockAlertMonitor] ALERT: ${ticker} in #${channelName}`);

        // Play sound
        playAlertSound();

        // Show notification
        showNotification(ticker, fullMatch, channelName);

        // Send to webhook
        sendToWebhook(ALERT_WEBHOOK_URL, {
            ticker,
            price_info: fullMatch,
            channel: channelName,
            content: message.content,
            timestamp
        });
    }
}

// ============== Backfill Widget Functions ==============

function getVisibleMessageTimestamps(): { first: string | null; last: string | null; count: number } {
    const timeElements = document.querySelectorAll("time[datetime]");
    if (timeElements.length === 0) {
        return { first: null, last: null, count: 0 };
    }

    const first = timeElements[0].getAttribute("datetime");
    const last = timeElements[timeElements.length - 1].getAttribute("datetime");

    return { first, last, count: timeElements.length };
}

function formatTimestamp(isoString: string | null): string {
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

function getCurrentChannelName(): string {
    const channelId = SelectedChannelStore.getChannelId();
    if (!channelId) return "unknown";
    const channel = ChannelStore.getChannel(channelId);
    return channel?.name || "unknown";
}

function getVisibleMessages(): Array<{ id: string; content: string; timestamp: string }> {
    const messages: Array<{ id: string; content: string; timestamp: string }> = [];

    // Find all message list items
    const messageElements = document.querySelectorAll('li[id^="chat-messages-"]');

    messageElements.forEach((msgEl) => {
        const id = msgEl.id.replace("chat-messages-", "");

        // Get timestamp
        const timeEl = msgEl.querySelector("time[datetime]");
        const timestamp = timeEl?.getAttribute("datetime") || "";

        // Get message content - look for the message content div
        const contentEl = msgEl.querySelector('div[id^="message-content-"]');
        const content = contentEl?.textContent?.trim() || "";

        if (content && timestamp) {
            messages.push({ id, content, timestamp });
        }
    });

    return messages;
}

function updateWidgetDisplay() {
    if (!widgetContainer) return;

    const timestamps = getVisibleMessageTimestamps();
    const channel = getCurrentChannelName();

    const firstSpan = widgetContainer.querySelector("#backfill-first") as HTMLSpanElement;
    const lastSpan = widgetContainer.querySelector("#backfill-last") as HTMLSpanElement;
    const countSpan = widgetContainer.querySelector("#backfill-count") as HTMLSpanElement;
    const channelSpan = widgetContainer.querySelector("#backfill-channel") as HTMLSpanElement;

    if (firstSpan) firstSpan.textContent = formatTimestamp(timestamps.first);
    if (lastSpan) lastSpan.textContent = formatTimestamp(timestamps.last);
    if (countSpan) countSpan.textContent = String(timestamps.count);
    if (channelSpan) channelSpan.textContent = `#${channel}`;
}

async function handleSendData() {
    const statusEl = widgetContainer?.querySelector("#backfill-status") as HTMLSpanElement;
    const channel = getCurrentChannelName();
    const messages = getVisibleMessages();

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

    const success = await sendToWebhook(BACKFILL_WEBHOOK_URL, {
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

function createBackfillWidget() {
    // Remove existing widget if any
    removeBackfillWidget();

    widgetContainer = document.createElement("div");
    widgetContainer.id = "stock-alert-backfill-widget";
    widgetContainer.innerHTML = `
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

    document.body.appendChild(widgetContainer);

    // Add event listeners
    const closeBtn = widgetContainer.querySelector("#backfill-close");
    closeBtn?.addEventListener("click", () => {
        if (widgetContainer) widgetContainer.style.display = "none";
    });

    const sendBtn = widgetContainer.querySelector("#backfill-send");
    sendBtn?.addEventListener("click", handleSendData);

    // Set up scroll listener on the message scroller
    scrollHandler = () => {
        updateWidgetDisplay();
    };

    // Listen for scroll on the chat scroller - use a debounced handler
    let scrollTimeout: number | null = null;
    const debouncedScroll = () => {
        if (scrollTimeout) clearTimeout(scrollTimeout);
        scrollTimeout = window.setTimeout(updateWidgetDisplay, 100);
    };

    // Find the chat scroller and attach listener
    const chatScroller = document.querySelector('[class*="scroller"]');
    if (chatScroller) {
        chatScroller.addEventListener("scroll", debouncedScroll);
    }

    // Also listen for any scroll in the document
    document.addEventListener("scroll", debouncedScroll, true);

    // Initial update
    updateWidgetDisplay();

    // Update periodically in case DOM changes
    setInterval(updateWidgetDisplay, 2000);

    console.log("[StockAlertMonitor] Backfill widget created");
}

function removeBackfillWidget() {
    if (widgetContainer) {
        widgetContainer.remove();
        widgetContainer = null;
    }
}

// ============== Plugin Definition ==============

export default definePlugin({
    name: "StockAlertMonitor",
    description: "Monitors Discord channels for stock alerts (TICKER < $X pattern) + backfill widget",
    authors: [Devs.Ven], // Required field

    start() {
        console.log("[StockAlertMonitor] Starting...");
        FluxDispatcher.subscribe("MESSAGE_CREATE", handleMessage);

        // Request notification permission
        if (Notification.permission === "default") {
            Notification.requestPermission();
        }

        // Create backfill widget after a short delay to ensure DOM is ready
        setTimeout(createBackfillWidget, 2000);
    },

    stop() {
        console.log("[StockAlertMonitor] Stopping...");
        FluxDispatcher.unsubscribe("MESSAGE_CREATE", handleMessage);
        removeBackfillWidget();
    }
});
