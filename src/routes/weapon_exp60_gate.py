"""Match doorway contours by position and direction, without color correlation."""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


GATE_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / 'assets/checkpoints/weapon_exp60_gate.png'
GATE_TEMPLATE_RECT = (398, 82, 476, 282)
GATE_SEARCH_RECT = (240, 20, 880, 430)
GATE_THRESHOLD = 0.80
GATE_SCALES = tuple(0.8 + index * 0.05 for index in range(9))
GATE_UPPER_RECT = (0, 0, 78, 120)
GATE_INNER_RECT = (20, 25, 68, 165)
EDGE_TOLERANCE = 3
EDGE_DIRECTION_BINS = 4


@dataclass(frozen=True)
class GateMatch:
    score: float
    position: tuple[int, int] | None = None
    scale: float = 1.0
    upper_score: float = 0.0
    inner_score: float = 0.0

    @property
    def found(self):
        return self.score >= GATE_THRESHOLD


def contour_features(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 40, 100) > 0
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    # Ignore gradient polarity: a bright-to-dark edge has the same shape as a
    # dark-to-bright edge. Intensities are used only to extract these contours.
    angles = np.mod(cv2.phase(gradient_x, gradient_y, angleInDegrees=True), 180)
    directions = np.floor(angles * EDGE_DIRECTION_BINS / 180).astype(np.uint8) % EDGE_DIRECTION_BINS
    return edges, directions


def contour_masks(patch):
    edges, directions = contour_features(patch)
    count = int(edges.sum())
    if count == 0:
        raise ValueError('门框局部模板没有有效轮廓')
    masks = tuple((edges & (directions == direction)).astype(np.float32)
                  for direction in range(EDGE_DIRECTION_BINS))
    return masks, count


class GateMatcher:
    def __init__(self, template_path=GATE_TEMPLATE_PATH):
        template = cv2.imdecode(np.fromfile(template_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if template is None or template.shape[:2] != (200, 78):
            raise ValueError('门框检查模板缺失或无有效特征')
        self.templates = []
        for scale in GATE_SCALES:
            scaled = cv2.resize(template, (round(78 * scale), round(200 * scale)))
            upper = scaled[:round(GATE_UPPER_RECT[3] * scale)]
            x_offset, y_offset = round(GATE_INNER_RECT[0] * scale), round(GATE_INNER_RECT[1] * scale)
            inner = scaled[y_offset:round(GATE_INNER_RECT[3] * scale),
                           x_offset:round(GATE_INNER_RECT[2] * scale)]
            self.templates.append((scale, x_offset, y_offset, contour_masks(upper), contour_masks(inner)))

    @staticmethod
    def coverage_map(nearby_edges, part):
        masks, count = part
        scores = None
        for nearby, mask in zip(nearby_edges, masks):
            # Correlate binary edge masks, never pixel colors or brightness.
            matched = cv2.matchTemplate(nearby, mask, cv2.TM_CCORR)
            scores = matched if scores is None else scores + matched
        return np.clip(scores / count, 0.0, 1.0)

    def match(self, frame):
        if frame is None or frame.size == 0:
            raise ValueError('门框检查未取得有效画面')
        normalized = cv2.resize(frame, (1600, 900))
        edges, directions = contour_features(normalized)
        left, top, right, bottom = GATE_SEARCH_RECT
        margin = EDGE_TOLERANCE
        region_edges = edges[top - margin:bottom + margin, left - margin:right + margin]
        region_directions = directions[top - margin:bottom + margin, left - margin:right + margin]
        if not region_edges.any():
            return GateMatch(0.0)
        nearby_edges = []
        for direction in range(EDGE_DIRECTION_BINS):
            eligible = region_edges & (region_directions == direction)
            if eligible.any():
                distance = cv2.distanceTransform((~eligible).astype(np.uint8) * 255, cv2.DIST_L2, 3)
                nearby = (distance <= EDGE_TOLERANCE).astype(np.float32)
            else:
                nearby = np.zeros(eligible.shape, dtype=np.float32)
            nearby_edges.append(nearby[margin:-margin, margin:-margin])
        best = GateMatch(0.0)
        for scale, x_offset, y_offset, upper, inner in self.templates:
            upper_map = self.coverage_map(nearby_edges, upper)
            inner_map = self.coverage_map(nearby_edges, inner)
            height = min(upper_map.shape[0], inner_map.shape[0] - y_offset)
            width = min(upper_map.shape[1], inner_map.shape[1] - x_offset)
            upper_map = upper_map[:height, :width]
            inner_map = inner_map[y_offset:y_offset + height, x_offset:x_offset + width]
            # Parts must agree on one door anchor and scale; a strong unrelated
            # edge elsewhere cannot compensate for a missing part of the door.
            scores = np.minimum(upper_map, inner_map)
            _, score, _, location = cv2.minMaxLoc(scores)
            if np.isfinite(score) and score > best.score:
                x, y = location
                best = GateMatch(float(score), (left + x, top + y), scale,
                                 float(upper_map[y, x]), float(inner_map[y, x]))
        return best
