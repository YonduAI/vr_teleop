#!/usr/bin/env python3
import logging
import time
from robot_webrtc_lib import RobotWebRTCConfig, RobotWebRTCNode

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = RobotWebRTCConfig(
        ws_url="ws://13.56.253.215:3000",
        token="supersecret123",
        room="testroom",
        role="robot",
        name="orin",
        enable_video=True,
        capture_fps=100,
        send_fps=100,   # set 50 if you’re testing in browsers
    )

    node = RobotWebRTCNode(cfg)
    node.start()

    print("Streaming... Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()

if __name__ == "__main__":
    main()
