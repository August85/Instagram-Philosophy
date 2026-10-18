"""Find human faces in pictures, so backgrounds without people can be kept.

On a Mac this uses Apple's built-in Vision framework through a small Swift
script (no extra installs). Elsewhere it uses OpenCV if it happens to be
installed. If neither is available, count_faces returns None and the caller
keeps the pictures for a review by eye.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

SWIFT = r'''
import Foundation
import Vision
var out: [String: Int] = [:]
for path in CommandLine.arguments.dropFirst() {
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:])
    let faces = VNDetectFaceRectanglesRequest()
    do {
        try handler.perform([faces])
        out[path] = faces.results?.count ?? 0
    } catch {
        out[path] = -1
    }
}
let data = try! JSONSerialization.data(withJSONObject: out)
print(String(data: data, encoding: .utf8)!)
'''


def _vision(paths: list[Path]) -> dict[str, int] | None:
    if sys.platform != "darwin" or not shutil.which("swift"):
        return None
    script = Path(__file__).with_name("_faces.swift")
    if not script.exists() or script.read_text() != SWIFT:
        script.write_text(SWIFT)
    try:
        r = subprocess.run(["swift", str(script), *map(str, paths)], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return {k: int(v) for k, v in json.loads(r.stdout.strip().splitlines()[-1]).items()}
    except (ValueError, IndexError):
        return None


def _opencv(paths: list[Path]) -> dict[str, int] | None:
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    out = {}
    for p in paths:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        out[str(p)] = -1 if img is None else len(cascade.detectMultiScale(img, 1.1, 5, minSize=(24, 24)))
    return out


def count_faces(paths: list[Path]) -> dict[str, int] | None:
    """{path: number of faces} (-1 if the file couldn't be read), or None if no detector is available."""
    if not paths:
        return {}
    return _vision(paths) or _opencv(paths)
