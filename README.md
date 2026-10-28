# SitRight

A small tray app that watches your posture through the webcam and taps you on the shoulder when you slouch, lean into the screen, stop blinking, or sit for too long.

<!-- demo GIF goes here -->

Everything runs on your own computer. Frames are processed in memory and thrown away; nothing is saved or uploaded.

## What it reminds you about

| Reminder | When |
| --- | --- |
| Sit up straight | your head stays noticeably lower than your calibrated pose for 8 seconds |
| Lean back | your face gets about 25% bigger than usual, meaning you moved close to the screen |
| Blink | fewer than 8 blinks a minute (people normally blink 15 to 20 times, much less when staring at a screen) |
| Take a break | 50 minutes at the desk without leaving for at least 2 minutes |

Each reminder shows up at most once every 5 minutes. The tray icon turns green when you're fine, amber when something's off, and grey when you're away or it's paused.

## Install

Needs Python 3.10 or newer and a webcam.

```bash
pip install git+https://github.com/A2M33/sitright
sitright
```

The first start downloads MediaPipe's face model (about 4 MB) and then asks you to sit the way you want to sit for a few seconds. That's your calibration. You can redo it any time from the tray menu.

On Linux you may need a tray extension for your desktop (for GNOME: AppIndicator) and `notify-send` for notifications.

### Start it with your computer

- Windows: press `Win + R`, type `shell:startup`, and put a shortcut to `sitright` in that folder.
- Linux: add `sitright` to your desktop's "Startup Applications".

## Preview mode

```bash
sitright --preview
```

Opens a camera window with live readings: posture, distance and blinks per minute. Press `C` to calibrate and `Q` to quit. Useful to check that detection works with your camera and lighting.

Other options: `--camera 1` to pick a different camera, `--version`.

If the preview shows a logo instead of you, camera 0 is probably a virtual camera from OBS, Snap Camera or similar. Try `--camera 1`, `--camera 2` and so on until you see yourself, then start the tray app with the same number.

## Tuning

Settings live in `%APPDATA%\sitright\config.json` on Windows and `~/.config/sitright/config.json` on Linux. Edit the numbers under `thresholds` and restart:

| Key | Default | Meaning |
| --- | --- | --- |
| `slouch_drop` | 0.07 | how far your head may drop, as a fraction of the camera image height |
| `too_close_ratio` | 1.25 | how much bigger your face may get before "lean back" |
| `hold_seconds` | 8 | how long bad posture must last before a reminder |
| `min_blinks_per_minute` | 8 | below this you get the blink reminder |
| `break_after_minutes` | 50 | time before the break reminder |
| `away_reset_seconds` | 120 | leaving this long counts as a break |
| `cooldown_seconds` | 300 | minimum gap between two identical reminders |

## How it works

[MediaPipe Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) finds 478 points on your face in each frame, about 12 times a second.

- **Posture** compares the height of your nose tip and the width of your face with the values saved during calibration. The webcam can't see your back, but when you slouch your head drops, and that's easy to measure.
- **Blinks** use the eye aspect ratio: eye height divided by eye width. It falls sharply when the eye closes. The cut-off is 70% of your own open-eye value, so it adapts to your eyes and glasses.

The decision logic is in `src/sitright/monitor.py` and has no camera code in it, so it's tested with fake measurements:

```bash
pip install -e ".[dev]"
pytest
```

## Privacy

- No images are written to disk or sent anywhere.
- The only network request is the one-time model download from Google's storage.
- Pausing from the tray menu releases the camera, so its light goes off.

## License

MIT
