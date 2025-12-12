/*
 * Vencord plugin: Stock Alert Monitor
 * Monitors Discord channels for stock alerts (TICKER < $X pattern)
 *
 * Installation:
 * 1. Enable "Vencord Desktop" in Vencord settings
 * 2. Copy this file to: ~/.config/Vencord/plugins/stockAlertMonitor.ts
 *    (or on macOS: ~/Library/Application Support/Vencord/plugins/)
 * 3. Restart Discord and enable the plugin in Vencord settings
 */

import { Devs } from "@utils/constants";
import definePlugin from "@utils/types";
import { FluxDispatcher } from "@webpack/common";

const CHANNEL_FILTER = ["pr-spike", "select-news"]; // Channels to monitor
const WEBHOOK_URL = "http://localhost:8765/alert";

// Track seen messages to avoid duplicates
const seenMessages = new Set<string>();

// Alert sound
let alertSound: HTMLAudioElement | null = null;

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

async function sendToWebhook(data: object) {
    try {
        await fetch(WEBHOOK_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
    } catch {
        // Webhook not running - that's OK
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
    const channel = (window as any).DiscordNative?.nativeModules?.getChannel?.(channelId);
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
        sendToWebhook({
            ticker,
            price_info: fullMatch,
            channel: channelName,
            content: message.content,
            timestamp
        });
    }
}

export default definePlugin({
    name: "StockAlertMonitor",
    description: "Monitors Discord channels for stock alerts (TICKER < $X pattern)",
    authors: [Devs.Ven], // Required field

    start() {
        console.log("[StockAlertMonitor] Starting...");
        FluxDispatcher.subscribe("MESSAGE_CREATE", handleMessage);

        // Request notification permission
        if (Notification.permission === "default") {
            Notification.requestPermission();
        }
    },

    stop() {
        console.log("[StockAlertMonitor] Stopping...");
        FluxDispatcher.unsubscribe("MESSAGE_CREATE", handleMessage);
    }
});
