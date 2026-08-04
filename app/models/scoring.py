"""FPL scoring constants, shared by training and serving.

Kept free of torch and numpy so both the PyTorch training path and the
onnxruntime serving path can import the same numbers. A scoring change is an
edit here rather than a hunt through two implementations.
"""

from __future__ import annotations

# Points per event, in the order the event head emits them:
# goals, assists, clean sheet, saves (per three), bonus, cards.
EVENT_POINTS: tuple[float, ...] = (5.0, 3.0, 1.0, 1.0 / 3.0, 1.0, -1.0)

N_EVENT_RATES = len(EVENT_POINTS)

# start, substitute, unused
N_APPEARANCE_CLASSES = 3

MAX_MINUTES = 90.0

# An appearance scores in its own right, before any event.
POINTS_FOR_START = 2.0
POINTS_FOR_SUBSTITUTE = 1.0

# The served ONNX graph emits the raw heads rather than a composed total, so
# that sampling the distribution can happen outside the graph. This is the
# layout of that output vector.
HEAD_LAYOUT = {
    "appearance_logits": (0, N_APPEARANCE_CLASSES),
    "minutes_if_start": (N_APPEARANCE_CLASSES, N_APPEARANCE_CLASSES + 1),
    "minutes_if_sub": (N_APPEARANCE_CLASSES + 1, N_APPEARANCE_CLASSES + 2),
    "event_rates": (
        N_APPEARANCE_CLASSES + 2,
        N_APPEARANCE_CLASSES + 2 + N_EVENT_RATES,
    ),
}
HEAD_WIDTH = N_APPEARANCE_CLASSES + 2 + N_EVENT_RATES
