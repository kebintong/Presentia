"""Raw V4L2 capture fallback (Linux only).

Virtual cameras like Iriun / OBS use v4l2loopback, which locks the stream
format while a producer (the phone) and other consumers (e.g. a browser in a
Meet call) are attached. OpenCV insists on renegotiating the format when it
opens a device, gets EBUSY, and gives up. This reader instead accepts the
device's current format as-is and pulls frames with plain read(), which
v4l2loopback supports alongside other consumers.
"""

from __future__ import annotations

import errno
import fcntl
import os
import struct
import time

import cv2
import numpy as np

_VIDIOC_G_FMT = 0xC0D05604
_V4L2_BUF_TYPE_VIDEO_CAPTURE = 1

# fourcc -> (buffer reshape, cv2 conversion)
_CONVERTERS = {
    "YU12": lambda d, w, h: cv2.cvtColor(d.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_I420),
    "YV12": lambda d, w, h: cv2.cvtColor(d.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_YV12),
    "NV12": lambda d, w, h: cv2.cvtColor(d.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_NV12),
    "YUYV": lambda d, w, h: cv2.cvtColor(d.reshape(h, w, 2), cv2.COLOR_YUV2BGR_YUY2),
    "BGR3": lambda d, w, h: d.reshape(h, w, 3).copy(),
    "RGB3": lambda d, w, h: cv2.cvtColor(d.reshape(h, w, 3), cv2.COLOR_RGB2BGR),
    "MJPG": lambda d, w, h: cv2.imdecode(d, cv2.IMREAD_COLOR),
}


class RawV4L2Capture:
    """Minimal cv2.VideoCapture-compatible reader for v4l2loopback devices."""

    def __init__(self, device: str, read_timeout: float = 2.0) -> None:
        self._timeout = read_timeout
        self._fd: int | None = None
        try:
            fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            return
        try:
            fmt = bytearray(208)
            struct.pack_into("<I", fmt, 0, _V4L2_BUF_TYPE_VIDEO_CAPTURE)
            fcntl.ioctl(fd, _VIDIOC_G_FMT, fmt)
            self._width, self._height, fourcc_i = struct.unpack_from("<III", fmt, 8)
            self._sizeimage = struct.unpack_from("<I", fmt, 28)[0]
            self._fourcc = struct.pack("<I", fourcc_i).decode(errors="replace")
            self._convert = _CONVERTERS.get(self._fourcc)
            if (
                self._convert is None
                or self._width <= 0
                or self._height <= 0
                or self._sizeimage <= 0
            ):
                os.close(fd)
                return
        except OSError:
            os.close(fd)
            return
        self._fd = fd

    # ---------------------------------------------- cv2-compatible surface

    def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2.VideoCapture
        return self._fd is not None

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._fd is None:
            return False, None
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                data = os.read(self._fd, self._sizeimage)
            except OSError as exc:
                if exc.errno != errno.EAGAIN:
                    return False, None
                time.sleep(0.01)
                continue
            if self._fourcc != "MJPG" and len(data) != self._sizeimage:
                time.sleep(0.01)
                continue
            arr = np.frombuffer(data, dtype=np.uint8)
            try:
                frame = self._convert(arr, self._width, self._height)
            except (cv2.error, ValueError):
                return False, None
            return (frame is not None), frame
        return False, None

    def set(self, *_args) -> bool:  # noqa: D102 - format is fixed by the producer
        return False

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
