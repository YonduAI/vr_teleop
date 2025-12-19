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
        name="orin-cmd",
        enable_video=False,   # commands only
    )

    node = RobotWebRTCNode(cfg)
    node.start()

    print("Waiting for control messages... Ctrl+C to stop.")
    try:
        while True:
            msg = node.get_control(timeout=0.5)
            if msg:
                # server.js envelopes include type/roomId/from/payload
                print("CONTROL:", msg.get("from"), msg.get("payload"))
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()

if __name__ == "__main__":
    main()
