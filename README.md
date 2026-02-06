# Sublime Text Packages

This repository contains custom Sublime Text packages.

## Available Packages

### Kubeseal
Encrypt/decrypt strings with kubeseal for Kubernetes sealed secrets.

## Installation

### Using Package Control (Recommended)

1. **Install Package Control** (if not already installed):
   - Open Sublime Text
   - Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (Mac)
   - Type "Install Package Control" and press Enter

2. **Add this repository**:
   - Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (Mac)
   - Type "Package Control: Add Repository" and press Enter
   - Enter: `https://raw.githubusercontent.com/dspasic12/sublime-text-packages/main/repository.json`

3. **Install the package**:
   - Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (Mac)
   - Type "Package Control: Install Package" and press Enter
   - Search for "Kubeseal" and select it

### Manual Installation

1. Download the latest release from the [Releases page](https://github.com/dspasic12/sublime-text-packages/releases)
2. Extract the ZIP file
3. Copy the `kubeseal` folder to your Sublime Text `Packages` directory:

   | Platform | Path |
   |----------|------|
   | **Windows** | `%APPDATA%\Sublime Text\Packages\` |
   | **Mac** | `~/Library/Application Support/Sublime Text/Packages/` |
   | **Linux** | `~/.config/sublime-text/Packages/` |

4. Restart Sublime Text

## Usage

### Kubeseal Package

After installation, you can access Kubeseal commands via:

#### Command Palette
Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (Mac), then type:
- `Kubeseal: Encrypt String`
- `Kubeseal: Decrypt String`

#### Menu
Go to **Tools → Kubeseal** and select your desired action

## Requirements

- Sublime Text 3 or higher
- `kubeseal` binary installed and available in your system PATH

## Issues

Report issues at: [GitHub Issues](https://github.com/dspasic12/sublime-text-packages/issues)

## License

[Add your license information here]
