# VR Teleop - WebRTC Robot Control & Video Streaming

A complete WebRTC-based teleoperation system for robots using NVIDIA Jetson AGX Orin devices. This project provides real-time video streaming and bidirectional control messaging through WebRTC with WebSocket-based signaling.

## Features

- **Hardware-accelerated video encoding**: Uses NVIDIA's `nvv4l2h264enc` for efficient H.264 encoding
- **WebRTC video streaming**: Full WebRTC implementation with GStreamer's `webrtcbin`
- **Bidirectional control plane**: Send and receive control commands via WebSocket signaling
- **Role-based routing**: Signaling server routes messages based on client roles (robot, controller, viewer)
- **Lazy pipeline**: Video pipeline starts only when a viewer connects
- **Library-based architecture**: Reusable `robot_webrtc_lib.py` for easy integration
- **Multiple viewers**: Support for multiple viewers with automatic retargeting

## Architecture

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│   Robot     │◄───────►│   Signaling  │◄───────►│  Controller│
│  (Jetson)   │  WebRTC │    Server    │  WebRTC │  / Viewer   │
└─────────────┘         └──────────────┘         └─────────────┘
     │                        │                        │
     └────────────────────────┴────────────────────────┘
                    WebSocket Signaling
```

- **Robot**: Streams video and receives control commands
- **Signaling Server**: Routes WebRTC signaling and control messages
- **Controller/Viewer**: Sends control commands and/or receives video

## Requirements

### Hardware
- NVIDIA Jetson AGX Orin (or compatible Jetson device) for robot side
- USB or CSI camera connected to `/dev/video0` (or specify with config)
- Any Linux system for controller/viewer side (no NVIDIA hardware needed)

### Software (Jetson - Robot Side)

```bash
sudo apt-get update
sudo apt-get install -y \
  python3-gi \
  gir1.2-gst-plugins-bad-1.0 \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-nice

python3 -m pip install --upgrade websockets
```

### Software (Controller/Viewer Side - Any Linux)

Same as above (no NVIDIA hardware required for receiving video).

Or install Python dependencies from requirements.txt:

```bash
pip3 install -r requirements.txt
```

### Signaling Server (Node.js)

```bash
cd signaling-server
npm install
```

## Installation

1. Clone this repository:
```bash
git clone https://github.com/YonduAI/vr_teleop.git
cd vr_teleop
```

2. Install dependencies (see Requirements above)

3. Start the signaling server:
```bash
cd signaling-server
PORT=3000 SIGNALING_TOKEN=supersecret123 node server.js
```

4. Ensure your camera is connected and accessible on the robot

## Quick Start

### 1. Start the Robot (Video Streaming)

On your Jetson device:

```bash
python3 robot_stream_video.py
```

This will:
- Connect to the signaling server (default: `ws://13.56.253.215:3000`)
- Join room `testroom` as role `robot` with name `orin`
- Start streaming video when a viewer connects
- Wait for control commands

### 2. View the Video Stream

On any Linux machine:

```bash
python3 webrtc_reciever_v2.py
```

This will:
- Connect to the signaling server
- Join as role `viewer`
- Automatically target the first robot in the room
- Display the video stream with FPS statistics

### 3. Send Control Commands

**Minimal example** (send one command):
```bash
python3 send_control_tutorial.py --payload '{"cmd":"forward","speed":0.5}'
```

**Stress test** (send multiple commands):
```bash
python3 send_random_control.py --rate 60 --count 100
```

### 4. Receive Control Commands on Robot

On the robot (without video, commands only):

```bash
python3 robot_print_commands.py
```

This will print all incoming control messages.

## Usage Details

### Robot Video Streaming

`robot_stream_video.py` - Main robot video streaming script

```python
from robot_webrtc_lib import RobotWebRTCConfig, RobotWebRTCNode

cfg = RobotWebRTCConfig(
    ws_url="ws://your-server:3000",
    token="your-token",
    room="room-id",
    role="robot",
    name="orin",
    enable_video=True,
    capture_fps=100,
    send_fps=100,
)
node = RobotWebRTCNode(cfg)
node.start()
```

**Configuration options:**
- `enable_video`: Enable/disable video streaming
- `capture_fps`: Camera capture framerate
- `send_fps`: WebRTC send framerate (lower for browser compatibility)
- `width`, `height`: Video resolution
- `bitrate`: H.264 encoding bitrate
- `offerer`: If True, robot creates offer (default: False, waits for viewer offer)

### Robot Control Receiver

`robot_print_commands.py` - Example control message receiver

```python
from robot_webrtc_lib import RobotWebRTCConfig, RobotWebRTCNode

cfg = RobotWebRTCConfig(
    ws_url="ws://your-server:3000",
    token="your-token",
    room="room-id",
    role="robot",
    name="orin-cmd",
    enable_video=False,  # Commands only
)
node = RobotWebRTCNode(cfg)
node.start()

while True:
    msg = node.get_control(timeout=0.5)
    if msg:
        print("CONTROL:", msg.get("payload"))
```

### Sending Control Commands

**Tutorial script** (`send_control_tutorial.py`):
```bash
python3 send_control_tutorial.py \
  --ws ws://your-server:3000 \
  --token your-token \
  --room room-id \
  --target-name orin-cmd \
  --payload '{"cmd":"forward","speed":0.5}'
```

**Random control sender** (`send_random_control.py`):
```bash
python3 send_random_control.py \
  --rate 60 \
  --count 100 \
  --target-role robot
```

### Video Receiver

`webrtc_reciever_v2.py` - CLI video receiver

```bash
python3 webrtc_reciever_v2.py \
  --ws ws://your-server:3000 \
  --token your-token \
  --room room-id \
  --target-name orin \
  --target-role robot
```

## Signaling Server Protocol

### Authentication
```json
{
  "type": "auth",
  "payload": {"token": "YOUR_TOKEN"}
}
```

Response:
```json
{
  "type": "auth-ok",
  "payload": {"clientId": "unique-id"}
}
```

### Join Room
```json
{
  "type": "join",
  "roomId": "room-id",
  "payload": {
    "role": "robot",  // or "controller", "viewer"
    "name": "orin"
  }
}
```

Response:
```json
{
  "type": "joined",
  "payload": {
    "peers": [
      {"clientId": "id1", "role": "robot", "name": "orin"},
      {"clientId": "id2", "role": "viewer", "name": "viewer-cli"}
    ]
  }
}
```

### WebRTC Signaling
```json
{
  "type": "offer",  // or "answer"
  "roomId": "room-id",
  "to": "target-client-id",  // optional: direct to specific peer
  "payload": {
    "sdp": "<SDP string>",
    "sdpType": "offer"
  }
}
```

```json
{
  "type": "ice-candidate",
  "roomId": "room-id",
  "to": "target-client-id",
  "payload": {
    "candidate": "<ICE candidate string>",
    "sdpMLineIndex": 0
  }
}
```

### Control Messages
```json
{
  "type": "control",
  "roomId": "room-id",
  "to": "target-client-id",  // optional: broadcast if omitted
  "payload": {
    "cmd": "forward",
    "speed": 0.5,
    "timestamp": 1234567890
  }
}
```

## GStreamer Pipeline

The robot video pipeline uses NVIDIA hardware acceleration:

```
v4l2src → nvvidconv → nvv4l2h264enc → h264parse → rtph264pay → webrtcbin
```

Key features:
- **nvvidconv**: NVIDIA video converter for format conversion
- **nvv4l2h264enc**: Hardware H.264 encoder with configurable bitrate
- **H.264 Baseline Profile**: Ensures maximum compatibility
- **Zero-latency RTP**: Optimized for real-time streaming
- **Lazy start**: Pipeline only starts when a viewer connects

## Library API

### `RobotWebRTCNode`

Main class for robot-side WebRTC functionality.

**Methods:**
- `start()`: Start the node (connect to signaling, start pipeline if video enabled)
- `stop()`: Stop the node and cleanup
- `get_control(timeout=0.5)`: Get next control message (returns dict or None)
- `send_telemetry(data)`: Send telemetry data to controllers

**Properties:**
- `cfg`: Configuration object
- `is_connected`: Whether signaling is connected

### `RobotWebRTCConfig`

Configuration dataclass for robot nodes.

**Key fields:**
- `ws_url`: WebSocket signaling server URL
- `token`: Authentication token
- `room`: Room ID
- `role`: Client role ("robot", "controller", "viewer")
- `name`: Client name
- `enable_video`: Enable video streaming
- `device`: Camera device path
- `width`, `height`: Video resolution
- `capture_fps`, `send_fps`: Frame rates
- `bitrate`: H.264 bitrate
- `stun`, `turn`: STUN/TURN server URIs

## Behavior Notes

- **Lazy pipeline**: The robot video pipeline starts only when a viewer sends an offer. This keeps the camera/encoder idle when no one is watching.
- **Multiple viewers**: The robot retargets to the latest offer; earlier viewers will be dropped.
- **Target selection**: Viewers can prefer a robot by name (`--target-name`) or role (`--target-role`), or pin a specific `--target` clientId.
- **Control routing**: The signaling server routes `control` messages to robots based on role or explicit `to` field.
- **Auto-reconnect**: The signaling client automatically reconnects on connection loss.

## Files

### Main Scripts
- `robot_stream_video.py` - Robot video streaming (uses library)
- `robot_print_commands.py` - Robot control command receiver example
- `webrtc_reciever_v2.py` - CLI video receiver for Linux
- `send_control_tutorial.py` - Minimal one-shot control sender tutorial
- `send_random_control.py` - Control stress sender (rate/count/duration)

### Library
- `robot_webrtc_lib.py` - Shared robot WebRTC/signaling/pipeline library

### Server
- `signaling-server/server.js` - WebSocket signaling server (Node.js)
- `signaling-server/package.json` - Node.js dependencies

### Legacy
- `simple_streamer_old/` - Old standalone streamer/receiver (deprecated)

### Config
- `requirements.txt` - Python dependencies

## Troubleshooting

### Camera not found
- Check camera is connected: `ls -la /dev/video*`
- Verify permissions: `sudo chmod 666 /dev/video0`
- Update device path in config if needed

### Connection issues
- Verify signaling server URL and token
- Check firewall settings for WebSocket connections
- Enable verbose logging: Set logging level to DEBUG
- Consider using TURN server for NAT traversal

### Encoding errors
- Ensure NVIDIA drivers are up to date
- Check GStreamer plugins: `gst-inspect-1.0 nvv4l2h264enc`
- Reduce bitrate if hardware encoding fails
- Check camera format compatibility

### Control messages not received
- Verify robot is joined with correct role (`robot`)
- Check controller is using correct `--target-role` or `--target-name`
- Ensure signaling server is routing messages correctly
- Check robot is calling `get_control()` regularly

### Video not streaming
- Verify viewer is connected and has sent an offer
- Check robot `enable_video=True` in config
- Verify camera is accessible and working
- Check WebRTC connection state in logs

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]

## Support

For issues and questions, please open an issue on GitHub.
