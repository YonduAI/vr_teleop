#!/usr/bin/env python3
"""
Example: Receive video and display frames with OpenCV via on_frame callback.
"""

import logging

import gi
import cv2
import numpy as np

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # type: ignore

from teleop_core.video_receiver import VideoReceiverConfig, run_video_receiver


def on_frame(data: bytes, sample) -> None:
    """
    data: raw frame bytes (after videoconvert)
    sample: GstSample (for caps/metadata if needed)
    """
    buf = np.frombuffer(data, dtype=np.uint8)
    caps = sample.get_caps()
    s = caps.get_structure(0)
    width = s.get_value("width")
    height = s.get_value("height")
    # Assuming format=RGB from videoconvert (default). If you need BGR, swap channels.
    frame = buf.reshape((height, width, 3))
    cv2.imshow("WebRTC Viewer", frame)
    cv2.waitKey(1)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Gst.init(None)

    cfg = VideoReceiverConfig(
        ws_url="ws://13.56.253.215:3000",
        token="supersecret123",
        room="testroom",
        target_name="orin",   # prefer the video robot
        target_role="robot",
        on_frame=on_frame,    # hook to receive frames instead of displaying
    )
    run_video_receiver(cfg)


if __name__ == "__main__":
    main()
