# MillisecondTimestamps BetterDiscord Plugin

A BetterDiscord plugin that displays Discord message timestamps with millisecond precision instead of the default minute precision.

## Features

- **Millisecond Precision**: Shows timestamps in formats like `14:23:45.123` instead of just `14:23`
- **Multiple Format Options**:
  - `HH:mm:ss.SSS` - Standard 24-hour format with milliseconds (default)
  - `HH:mm:ss.SSS (UTC)` - UTC time with milliseconds
  - `mm:ss.SSS` - Minutes and seconds only with milliseconds
- **Automatic Detection**: Automatically finds and modifies Discord's timestamp elements
- **Settings Panel**: Easy configuration through BetterDiscord's plugin settings
- **Real-time Updates**: Handles dynamically loaded messages and channel switches

## Installation

1. Copy `MillisecondTimestamps.plugin.js` to your BetterDiscord plugins folder
2. Reload Discord (Ctrl+R)
3. Enable the plugin in BetterDiscord settings

## Usage

### Basic Usage
- Install and enable the plugin
- Message timestamps will automatically show millisecond precision
- Timestamps will appear as `HH:mm:ss.SSS` format (e.g., `14:23:45.123`)

### Configuration
Access the settings panel through BetterDiscord's plugin settings:

1. Open BetterDiscord Settings (gear icon)
2. Go to Plugins tab
3. Find "MillisecondTimestamps" and click the settings button
4. Configure:
   - **Enable/Disable**: Toggle the plugin on/off
   - **Timestamp Format**: Choose your preferred format
   - **Save Settings**: Apply changes
   - **Toggle Now**: Quick enable/disable without saving

### Format Examples

#### HH:mm:ss.SSS (Default)
- Today: `14:23:45.123`
- Yesterday: `Yesterday 14:23:45.123`
- Older: `12/15 14:23:45.123`

#### HH:mm:ss.SSS (UTC)
- Today: `19:23:45.123 (UTC)`
- Yesterday: `Yesterday 19:23:45.123 (UTC)`
- Older: `12/15 19:23:45.123 (UTC)`

#### mm:ss.SSS
- Today: `23:45.123`
- Yesterday: `Yesterday 23:45.123`
- Older: `12/15 23:45.123`

## Technical Details

The plugin works by:

1. **DOM Monitoring**: Uses a MutationObserver to watch for new `<time datetime="...">` elements
2. **Format Modification**: Parses the ISO timestamp from the `datetime` attribute and reformats the text content
3. **State Preservation**: Maintains original formatting for restoration when disabled
4. **Performance**: Debounces updates and uses efficient DOM queries

## Compatibility

- **BetterDiscord**: Required
- **Discord**: Works with current Discord versions
- **Themes**: Compatible with all BetterDiscord themes
- **Other Plugins**: Should work alongside other plugins

## Troubleshooting

### Timestamps not updating
- Try reloading Discord (Ctrl+R)
- Check that the plugin is enabled in BetterDiscord settings
- Switch channels to trigger a refresh

### Wrong time zone
- Use the "HH:mm:ss.SSS (UTC)" format for consistent UTC timestamps
- Check your system time zone settings

### Performance issues
- The plugin uses efficient DOM monitoring
- If issues persist, try disabling and re-enabling the plugin

## Development

The plugin uses:
- BetterDiscord's `BdApi` for Webpack access and UI
- MutationObserver for DOM changes
- Standard JavaScript Date parsing
- CSS for settings panel styling

## Changelog

### v1.0.0
- Initial release
- Millisecond timestamp formatting
- Multiple format options
- Settings panel
- Real-time updates for new messages
