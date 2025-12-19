/**
 * @name MillisecondTimestamps
 * @description Show Discord message timestamps with millisecond precision instead of minute precision
 * @version 1.0.0
 * @author lpender
 */

const { Webpack } = BdApi;
const { Patcher } = BdApi;

module.exports = class MillisecondTimestamps {
    constructor() {
        this.enabled = true;
        this.patches = [];
        this.settings = BdApi.Data.load("MillisecondTimestamps", "settings") || {
            enabled: true,
            format: "HH:mm:ss.SSS"
        };
    }

    start() {
        console.log("[MillisecondTimestamps] Starting...");

        // Load settings
        this.settings = BdApi.Data.load("MillisecondTimestamps", "settings") || {
            enabled: true,
            format: "HH:mm:ss.SSS"
        };
        this.enabled = this.settings.enabled;

        if (this.enabled) {
            this.patchTimestamps();
        }

        BdApi.UI.showToast("Millisecond Timestamps started!", { type: "success" });
    }

    stop() {
        console.log("[MillisecondTimestamps] Stopping...");

        // Unpatch all timestamp patches
        this.unpatchTimestamps();

        BdApi.UI.showToast("Millisecond Timestamps stopped", { type: "info" });
    }

    patchTimestamps() {
        console.log("[MillisecondTimestamps] Setting up timestamp modifications...");

        // Use the DOM-based approach as it's more reliable for Discord's dynamic rendering
        this.patchTimestampElements();
    }

    patchTimestampElements() {
        console.log("[MillisecondTimestamps] Setting up DOM-based timestamp patching...");

        // Process existing timestamps first
        this.processExistingTimestamps();

        // Set up a mutation observer to watch for new timestamp elements
        this.timestampObserver = new MutationObserver((mutations) => {
            let hasNewContent = false;
            mutations.forEach((mutation) => {
                // Check for new nodes
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        hasNewContent = true;
                    }
                });

                // Check for attribute changes on existing elements
                if (mutation.type === 'attributes' && mutation.attributeName === 'datetime') {
                    hasNewContent = true;
                }
            });

            if (hasNewContent) {
                // Debounce processing to avoid excessive updates
                if (this.processTimeout) clearTimeout(this.processTimeout);
                this.processTimeout = setTimeout(() => {
                    this.processExistingTimestamps();
                }, 100);
            }
        });

        // Start observing the chat area specifically
        const chatContainer = document.querySelector('[data-list-id="chat-messages"]') ||
                             document.querySelector('[class*="messagesWrapper"]') ||
                             document.body;

        this.timestampObserver.observe(chatContainer, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['datetime']
        });

        console.log("[MillisecondTimestamps] Timestamp observer attached to:", chatContainer);

        // Also set up a periodic check in case the observer misses updates
        this.periodicCheck = setInterval(() => {
            this.processExistingTimestamps();
        }, 5000);
    }

    processTimestampElements(rootElement) {
        // Find all time elements with datetime attribute
        const timeElements = rootElement.querySelectorAll ?
            rootElement.querySelectorAll('time[datetime]') :
            (rootElement.matches && rootElement.matches('time[datetime]') ? [rootElement] : []);

        timeElements.forEach(timeEl => {
            this.modifyTimestampElementDOM(timeEl);
        });
    }

    processExistingTimestamps() {
        // Process all existing timestamp elements
        const timeElements = document.querySelectorAll('time[datetime]');
        timeElements.forEach(timeEl => {
            this.modifyTimestampElementDOM(timeEl);
        });
    }

    modifyTimestampElement(reactElement) {
        // For React elements, modify the props or children
        if (reactElement.props && reactElement.props.children) {
            // Find timestamp-related props and modify them
            if (reactElement.props.timestamp || reactElement.props.time) {
                const timestamp = reactElement.props.timestamp || reactElement.props.time;
                if (timestamp) {
                    const formatted = this.formatTimestampWithMilliseconds(timestamp);
                    reactElement.props.children = formatted;
                }
            }
        }
    }

    modifyTimestampElementDOM(timeElement) {
        if (!timeElement || !this.enabled) return;

        const datetime = timeElement.getAttribute('datetime');
        if (!datetime) return;

        // Skip if already processed
        if (timeElement.getAttribute('data-millisecond-timestamp') === 'true') return;

        try {
            const date = new Date(datetime);
            if (isNaN(date.getTime())) return;

            const formatted = this.formatTimestampWithMilliseconds(date);

            // Store original text content
            if (!timeElement.hasAttribute('data-original-timestamp')) {
                timeElement.setAttribute('data-original-timestamp', timeElement.textContent);
            }

            // Update the text content while preserving the original datetime attribute
            timeElement.textContent = formatted;
            // Add a data attribute to mark it as modified
            timeElement.setAttribute('data-millisecond-timestamp', 'true');
        } catch (error) {
            console.error("[MillisecondTimestamps] Error modifying timestamp element:", error);
        }
    }

    formatTimestampWithMilliseconds(date) {
        if (!(date instanceof Date)) {
            date = new Date(date);
        }

        if (isNaN(date.getTime())) {
            return "Invalid Date";
        }

        const now = new Date();
        const isToday = date.toDateString() === now.toDateString();
        const isYesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000).toDateString() === date.toDateString();

        const hours = date.getHours().toString().padStart(2, '0');
        const minutes = date.getMinutes().toString().padStart(2, '0');
        const seconds = date.getSeconds().toString().padStart(2, '0');
        const milliseconds = date.getMilliseconds().toString().padStart(3, '0');

        let formattedTime = `${hours}:${minutes}:${seconds}.${milliseconds}`;

        // Handle different format options
        switch (this.settings.format) {
            case 'HH:mm:ss.SSS (UTC)':
                const utcDate = new Date(date.getTime() + (date.getTimezoneOffset() * 60000));
                const utcHours = utcDate.getHours().toString().padStart(2, '0');
                const utcMinutes = utcDate.getMinutes().toString().padStart(2, '0');
                const utcSeconds = utcDate.getSeconds().toString().padStart(2, '0');
                const utcMilliseconds = utcDate.getMilliseconds().toString().padStart(3, '0');
                formattedTime = `${utcHours}:${utcMinutes}:${utcSeconds}.${utcMilliseconds} (UTC)`;
                break;

            case 'mm:ss.SSS':
                formattedTime = `${minutes}:${seconds}.${milliseconds}`;
                break;

            case 'HH:mm:ss.SSS':
            default:
                // Keep the default format
                break;
        }

        // Add date prefix for older messages
        if (!isToday && !isYesterday) {
            const month = (date.getMonth() + 1).toString().padStart(2, '0');
            const day = date.getDate().toString().padStart(2, '0');
            formattedTime = `${month}/${day} ${formattedTime}`;
        } else if (isYesterday) {
            formattedTime = `Yesterday ${formattedTime}`;
        }

        return formattedTime;
    }

    unpatchTimestamps() {
        // Remove all patches
        this.patches.forEach(patch => {
            try {
                Patcher.unpatchAll(patch);
            } catch (error) {
                console.error("[MillisecondTimestamps] Error unpatching:", error);
            }
        });
        this.patches = [];

        // Clear any pending timeout
        if (this.processTimeout) {
            clearTimeout(this.processTimeout);
            this.processTimeout = null;
        }

        // Clear periodic check
        if (this.periodicCheck) {
            clearInterval(this.periodicCheck);
            this.periodicCheck = null;
        }

        // Disconnect observer
        if (this.timestampObserver) {
            this.timestampObserver.disconnect();
            this.timestampObserver = null;
        }

        // Restore original timestamps
        this.restoreOriginalTimestamps();
    }

    restoreOriginalTimestamps() {
        // Find all modified timestamp elements and restore them
        const modifiedElements = document.querySelectorAll('time[data-millisecond-timestamp="true"]');
        modifiedElements.forEach(timeEl => {
            // Restore original text content if available
            const originalText = timeEl.getAttribute('data-original-timestamp');
            if (originalText) {
                timeEl.textContent = originalText;
                timeEl.removeAttribute('data-original-timestamp');
            }

            // Remove the modification marker
            timeEl.removeAttribute('data-millisecond-timestamp');
        });
    }

    toggleEnabled() {
        this.enabled = !this.enabled;
        this.settings.enabled = this.enabled;
        this.saveSettings();

        if (this.enabled) {
            this.patchTimestamps();
            BdApi.UI.showToast("Millisecond timestamps ENABLED", { type: "success" });
        } else {
            this.unpatchTimestamps();
            BdApi.UI.showToast("Millisecond timestamps DISABLED", { type: "info" });
        }
    }

    saveSettings() {
        BdApi.Data.save("MillisecondTimestamps", "settings", this.settings);
    }

    getSettingsPanel() {
        const panel = document.createElement("div");
        panel.style.padding = "20px";
        panel.style.backgroundColor = "#2f3136";
        panel.style.borderRadius = "8px";
        panel.style.color = "#dcddde";

        panel.innerHTML = `
            <h3 style="margin-top: 0; margin-bottom: 20px; color: #fff;">Millisecond Timestamps Settings</h3>

            <div style="margin-bottom: 20px;">
                <label style="display: block; margin-bottom: 10px; font-weight: bold;">
                    <input type="checkbox" id="mts-enabled" ${this.settings.enabled ? 'checked' : ''}>
                    Enable millisecond timestamps
                </label>
                <div style="color: #b9bbbe; font-size: 14px;">
                    Show message timestamps with millisecond precision (HH:mm:ss.SSS format) instead of minute precision.
                </div>
            </div>

            <div style="margin-bottom: 20px;">
                <label style="display: block; margin-bottom: 10px; font-weight: bold;">
                    Timestamp Format:
                </label>
                <select id="mts-format" style="background: #40444b; color: #dcddde; border: 1px solid #202225; border-radius: 4px; padding: 8px; width: 200px;">
                    <option value="HH:mm:ss.SSS" ${this.settings.format === 'HH:mm:ss.SSS' ? 'selected' : ''}>HH:mm:ss.SSS</option>
                    <option value="HH:mm:ss.SSS (UTC)" ${this.settings.format === 'HH:mm:ss.SSS (UTC)' ? 'selected' : ''}>HH:mm:ss.SSS (UTC)</option>
                    <option value="mm:ss.SSS" ${this.settings.format === 'mm:ss.SSS' ? 'selected' : ''}>mm:ss.SSS</option>
                </select>
            </div>

            <div style="margin-bottom: 20px; padding: 15px; background: #40444b; border-radius: 4px;">
                <h4 style="margin-top: 0; color: #fff;">Preview:</h4>
                <div id="mts-preview" style="font-family: monospace; background: #2f3136; padding: 8px; border-radius: 4px; border: 1px solid #202225;">
                    ${this.formatTimestampWithMilliseconds(new Date())}
                </div>
            </div>

            <button id="mts-save" style="background: #5865f2; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: 500;">
                Save Settings
            </button>
            <button id="mts-toggle" style="background: ${this.enabled ? '#3ba55c' : '#ed4245'}; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: 500; margin-left: 10px;">
                ${this.enabled ? 'Disable' : 'Enable'} Now
            </button>
        `;

        // Add event listeners
        setTimeout(() => {
            const enabledCheckbox = panel.querySelector('#mts-enabled');
            const formatSelect = panel.querySelector('#mts-format');
            const previewDiv = panel.querySelector('#mts-preview');
            const saveButton = panel.querySelector('#mts-save');
            const toggleButton = panel.querySelector('#mts-toggle');

            // Update preview when format changes
            formatSelect.addEventListener('change', () => {
                const selectedFormat = formatSelect.value;
                this.settings.format = selectedFormat;

                // Update preview
                let previewText = this.formatTimestampWithMilliseconds(new Date());
                if (selectedFormat.includes('(UTC)')) {
                    const utcDate = new Date();
                    utcDate.setMinutes(utcDate.getMinutes() - utcDate.getTimezoneOffset());
                    previewText = this.formatTimestampWithMilliseconds(utcDate) + ' (UTC)';
                } else if (selectedFormat === 'mm:ss.SSS') {
                    const now = new Date();
                    const minutes = now.getMinutes().toString().padStart(2, '0');
                    const seconds = now.getSeconds().toString().padStart(2, '0');
                    const milliseconds = now.getMilliseconds().toString().padStart(3, '0');
                    previewText = `${minutes}:${seconds}.${milliseconds}`;
                }
                previewDiv.textContent = previewText;
            });

            saveButton.addEventListener('click', () => {
                this.settings.enabled = enabledCheckbox.checked;
                this.settings.format = formatSelect.value;
                this.saveSettings();

                // Apply changes immediately
                const wasEnabled = this.enabled;
                this.enabled = this.settings.enabled;

                if (this.enabled && !wasEnabled) {
                    this.patchTimestamps();
                } else if (!this.enabled && wasEnabled) {
                    this.unpatchTimestamps();
                }

                BdApi.UI.showToast("Settings saved!", { type: "success" });
            });

            toggleButton.addEventListener('click', () => {
                this.toggleEnabled();
                // Update button appearance
                toggleButton.textContent = this.enabled ? 'Disable Now' : 'Enable Now';
                toggleButton.style.background = this.enabled ? '#3ba55c' : '#ed4245';
            });
        }, 100);

        return panel;
    }
};
