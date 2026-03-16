# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

This project directory is currently empty/newly initialized. It is part of the roBit workspace alongside `../ESP32_150326`, which is a PlatformIO-based ESP32/Arduino firmware project.

## Expected Build System

If this follows the sibling project pattern, it will use **PlatformIO**:

```bash
pio run                  # Build
pio run -t upload        # Build and flash to device
pio test                 # Run unit tests
pio device monitor       # Serial monitor
```

Update this file once the project structure is established.
