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

## Web Browser Viewer

The included `viewer.html` provides a web-based client to receive and display the video stream from your Jetson device.

### Using the Web Viewer

1. **Start the streamer on your Jetson device** (in answerer mode - default):
```bash
python3 webrtc_streamer.py \
  --ws wss://your-signaling-server.com \
  --token YOUR_SHARED_SECRET \
  --room YOUR_ROOM_ID
```

2. **Open the viewer in a web browser**:
   - Option A: Open `viewer.html` directly in your browser (file:// protocol)
   - Option B: Serve it via a web server:
     ```bash
     # Using Python's built-in server
     python3 -m http.server 8000
     # Then open http://localhost:8000/viewer.html
     ```

3. **Configure the viewer**:
   - Enter your signaling server URL (e.g., `wss://your-signaling-server.com`)
   - Enter your authentication token
   - Enter the room ID (must match the room ID used by the streamer)

4. **Connect**:
   - Click the "Connect" button
   - The viewer will authenticate, join the room, and create a WebRTC offer
   - The Jetson device will respond with an answer and start streaming
   - Video should appear in the browser

### Viewer Features

- **Real-time video playback**: Displays the H.264 video stream from the Jetson device
- **Connection status**: Shows WebRTC connection state and ICE gathering status
- **Statistics**: Displays inbound video statistics (bytes received, packets received)
- **Logging**: Real-time log of connection events and errors

### Typical Workflow

1. **Jetson device** (sender): Runs `webrtc_streamer.py` in answerer mode (default)
2. **Web browser** (receiver): Opens `viewer.html` and creates an offer
3. **Signaling server**: Relays the offer to Jetson, which responds with an answer
4. **WebRTC connection**: Established, video streams from Jetson to browser

### Notes

- The viewer acts as the **offerer** (creates the offer)
- The Jetson streamer acts as the **answerer** (responds with answer)
- Both must use the same signaling server URL, token, and room ID
- The viewer uses `recvonly` transceiver direction (receives video only)
- The streamer uses `sendonly` transceiver direction (sends video only)

## WebRTC Receiver

The `webrtc_reciever.py` is a Python-based receiver that can run on any Linux system (not just Jetson) to receive and test the video stream.

### Receiver Requirements

The receiver can run on **any Linux system** (Ubuntu, Debian, etc.) - it does not require NVIDIA hardware.

**Installation (Ubuntu/Debian):**

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

pip3 install websockets
```

Or install Python dependencies from requirements.txt:

```bash
pip3 install -r requirements.txt
```

**Usage:**

```bash
python3 webrtc_reciever.py \
  --ws wss://your-signaling-server.com \
  --token YOUR_SHARED_SECRET \
  --room YOUR_ROOM_ID
```

The receiver will:
- Connect to the signaling server and authenticate
- Join the specified room
- Create a WebRTC offer (recvonly)
- Receive H.264 video stream from the Jetson device
- Display FPS statistics (access units per second)

**Optional TURN server:**

```bash
python3 webrtc_reciever.py \
  --ws wss://your-signaling-server.com \
  --token YOUR_SHARED_SECRET \
  --room YOUR_ROOM_ID \
  --turn turn://user:pass@turn-server.com:3478?transport=udp
```

## Files

- `webrtc_streamer.py` - Main streaming application for Jetson Orin
- `webrtc_reciever.py` - WebRTC receiver implementation (runs on any Linux)
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

