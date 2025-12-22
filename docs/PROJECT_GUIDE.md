# VR Teleop – Project Guide

This repo provides a WebRTC video + control stack with clear sender/receiver roles. Use it as a reference for running the robot side, viewing video, and sending/receiving control messages.

## Components

- **Signaling server**: `signaling-server/server.js` (WebSocket auth/join, routes offer/answer/ICE, control/telemetry).
- **Libraries (teleop_core/)**:
  - `video_sender.py` (`RobotWebRTCConfig`, `RobotWebRTCNode`): robot video/telemetry sender + control receiver.
  - `video_receiver.py` (`VideoReceiverConfig`, `run_video_receiver`): viewer-side WebRTC receiver; supports `on_frame` callback.
  - `video_receiver_core.py`: full GStreamer/WebRTC receiver implementation.
  - `command_sender.py` (`ControlClientConfig`, `ControlClient`, `send_control_once`): controller-side command sender.
  - `command_receiver.py` (`ControlReceiverConfig`, `ControlReceiver`): control-only receiver (no video pipeline).
- **Scripts**:
  - `robot_stream_video.py`: CLI video sender for the robot (answerer by default; lazy pipeline start).
  - `robot_print_commands.py`: CLI control listener on the robot (no video).
  - `video_receiver_cli.py`: CLI video viewer (displays via fpsdisplaysink).
- **Examples (demos)**:
  - `examples/video_receiver_frames.py`: receive video and display/process with OpenCV via `on_frame`.
  - `examples/control_robot_receiver.py`: control-only listener using `teleop_core.command_receiver`.
  - `examples/control_controller_multi.py`: controller sending commands to multiple robots.
  - `send_control_tutorial.py`: minimal one-shot control sender.
  - `send_random_control.py`: control stress sender (rate/count/duration).

## Quick Start

1) **Robot video sender** (Jetson/robot):
```bash
python3 robot_stream_video.py \
  --ws ws://your-signal:3000 \
  --token your-token \
  --room your-room \
  --name robot-video
```
Pipeline starts when a viewer connects (offer arrives).

2) **Viewer (CLI)**:
```bash
python3 video_receiver_cli.py \
  --ws ws://your-signal:3000 \
  --token your-token \
  --room your-room \
  --target-name robot-video \
  --target-role robot
```

3) **Viewer (OpenCV frames)**:
```bash
python3 examples/video_receiver_frames.py \
  --ws ws://your-signal:3000 \
  --token your-token \
  --room your-room
```

4) **Control-only robot**:
```bash
python3 robot_print_commands.py \
  --ws ws://your-signal:3000 \
  --token your-token \
  --room your-room-cmd \
  --name robot-cmd
```

5) **Control sender (one-shot)**:
```bash
python3 send_control_tutorial.py \
  --ws ws://your-signal:3000 \
  --token your-token \
  --room your-room-cmd \
  --target-name robot-cmd \
  --payload '{"cmd":"demo","value":1}'
```

6) **Control stress test**:
```bash
python3 send_random_control.py --rate 60 --count 100 --target-name robot-cmd
```

## Import References

- Video send: `from teleop_core.video_sender import RobotWebRTCConfig, RobotWebRTCNode`
- Video receive (display): `from teleop_core.video_receiver import VideoReceiverConfig, run_video_receiver`
- Video receive (core): `from teleop_core.video_receiver_core import WebRTCReceiver`
- Control send: `from teleop_core.command_sender import ControlClientConfig, ControlClient, send_control_once`
- Control receive: `from teleop_core.command_receiver import ControlReceiverConfig, ControlReceiver`

## Behavior Notes

- **Lazy pipeline**: Robot video pipeline starts when an offer/answer/ICE arrives (answerer mode) and stops on disconnect.
- **Targeting**: Viewers pick a robot by `--target-name` (preferred) or `--target-role`; can pin `--target` clientId.
- **Multi-viewer**: Robot retargets to the latest offer; previous viewers are dropped.
- **Control routing**: Signaling server routes `control` to robots by role or explicit `to`. Robots must poll `get_control()`.
- **Reconnections**: Signaling clients auto-reconnect; pipelines restart on new offers.

## Installation

Dependencies (Ubuntu/Debian):
```bash
sudo apt-get update
sudo apt-get install -y python3-gi gir1.2-gst-plugins-bad-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-nice
pip3 install -r requirements.txt
```

## Tips

- For OpenCV viewing, ensure `opencv-python` and `numpy` are installed (included in requirements).
- If bandwidth is tight, lower `--bitrate` and `--send-fps` on the sender.
- Use `--offerer` on the sender only if the robot should initiate offers (default is answerer).
- Use `--verbose` to enable debug logs on any CLI script.

