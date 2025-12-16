# VR Teleop - WebRTC GStreamer Streaming for Jetson Orin AGX

A high-performance WebRTC video streaming solution for NVIDIA Jetson AGX Orin devices using GStreamer. This project enables real-time video streaming from a camera through WebRTC with WebSocket-based signaling.

## Features

- **Hardware-accelerated encoding**: Uses NVIDIA's `nvv4l2h264enc` for efficient H.264 encoding
- **WebRTC support**: Full WebRTC implementation with GStreamer's `webrtcbin`
- **WebSocket signaling**: Compatible with custom WebSocket signaling servers
- **Flexible roles**: Supports both offerer and answerer modes
- **ICE candidate buffering**: Handles ICE candidates that arrive before SDP
- **Configurable**: Command-line options for resolution, bitrate, FPS, and more
- **TURN/STUN support**: Configurable STUN and TURN servers for NAT traversal

## Requirements

### Hardware
- NVIDIA Jetson AGX Orin (or compatible Jetson device)
- USB or CSI camera connected to `/dev/video0` (or specify with `--device`)

### Software (Jetson)

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
  gstreamer1.0-libav

python3 -m pip install --upgrade websockets
```

Or install Python dependencies from requirements.txt:

```bash
pip3 install -r requirements.txt
```

## Installation

1. Clone this repository:
```bash
git clone https://github.com/YonduAI/vr_teleop.git
cd vr_teleop
```

2. Install dependencies (see Requirements above)

3. Ensure your camera is connected and accessible

## Usage

### Basic Usage (Answerer Mode - Default)

The streamer will wait for a remote offer and respond with an answer:

```bash
python3 webrtc_streamer.py \
  --ws wss://your-signaling-server.com \
  --token YOUR_SHARED_SECRET \
  --room YOUR_ROOM_ID
```

### Offerer Mode

To have the Orin device create the offer instead:

```bash
python3 webrtc_streamer.py \
  --ws wss://your-signaling-server.com \
  --token YOUR_SHARED_SECRET \
  --room YOUR_ROOM_ID \
  --offerer
```

### Advanced Options

```bash
python3 webrtc_streamer.py \
  --ws wss://your-signaling-server.com \
  --token YOUR_SHARED_SECRET \
  --room YOUR_ROOM_ID \
  --device /dev/video0 \
  --width 1344 \
  --height 376 \
  --fps 100 \
  --bitrate 12000000 \
  --iframeinterval 100 \
  --stun stun://stun.l.google.com:19302 \
  --turn turn://user:pass@turn-server.com:3478?transport=udp \
  --webrtc-latency 0 \
  --config-interval 1 \
  --verbose
```

### Command-Line Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--ws` | **required** | WebSocket URL of signaling server (e.g., `wss://example.com`) |
| `--token` | **required** | Authentication token for signaling server |
| `--room` | **required** | Room ID to join |
| `--device` | `/dev/video0` | Video device path |
| `--width` | `1344` | Video width in pixels |
| `--height` | `376` | Video height in pixels |
| `--fps` | `100` | Frames per second |
| `--bitrate` | `12000000` | H.264 bitrate in bits per second |
| `--iframeinterval` | `100` | I-frame interval (keyframe period) |
| `--stun` | `stun://stun.l.google.com:19302` | STUN server URI |
| `--turn` | (empty) | TURN server URI (e.g., `turn://user:pass@host:3478?transport=udp`) |
| `--offerer` | `False` | If set, Orin creates the offer instead of waiting for one |
| `--config-interval` | `1` | RTP H.264 config interval in seconds |
| `--webrtc-latency` | `0` | WebRTC latency in milliseconds |
| `--verbose` | `False` | Enable debug logging |

## Signaling Server Protocol

This implementation expects a WebSocket signaling server with the following message format:

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
  "type": "auth-ok"
}
```

### Join Room
```json
{
  "type": "join",
  "roomId": "room-id",
  "payload": {}
}
```

### SDP Offer/Answer
```json
{
  "type": "offer",  // or "answer"
  "roomId": "room-id",
  "payload": {
    "sdp": "<SDP string>",
    "sdpType": "offer"  // or "answer"
  }
}
```

### ICE Candidate
```json
{
  "type": "ice-candidate",
  "roomId": "room-id",
  "payload": {
    "candidate": "<ICE candidate string>",
    "sdpMLineIndex": 0
  }
}
```

## GStreamer Pipeline

The default pipeline uses NVIDIA hardware acceleration:

```
v4l2src → nvvidconv → nvv4l2h264enc → h264parse → rtph264pay → webrtcbin
```

Key features:
- **nvvidconv**: NVIDIA video converter for format conversion
- **nvv4l2h264enc**: Hardware H.264 encoder with configurable bitrate
- **H.264 Baseline Profile**: Ensures maximum compatibility
- **Zero-latency RTP**: Optimized for real-time streaming

## Files

- `webrtc_streamer.py` - Main streaming application for Jetson Orin
- `webrtc_reciever.py` - WebRTC receiver implementation
- `viewer.html` - Web-based viewer for testing
- `requirements.txt` - Python dependencies

## Troubleshooting

### Camera not found
- Check camera is connected: `ls -la /dev/video*`
- Verify permissions: `sudo chmod 666 /dev/video0`
- Try specifying device: `--device /dev/video1`

### Connection issues
- Verify signaling server URL and token
- Check firewall settings for WebSocket connections
- Enable `--verbose` for detailed logging
- Consider using TURN server for NAT traversal: `--turn turn://...`

### Encoding errors
- Ensure NVIDIA drivers are up to date
- Check GStreamer plugins: `gst-inspect-1.0 nvv4l2h264enc`
- Reduce bitrate if hardware encoding fails: `--bitrate 8000000`

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]

## Support

For issues and questions, please open an issue on GitHub.

