import math

import cv2
import numpy as np
from PIL import Image


def deskew_angle(img: Image.Image) -> float:
    """Estimate the rotation needed to straighten a scanned page.

    Uses ``cv2.minAreaRect`` on the thresholded non-white (dark) pixels.
    Returns the rotation angle in degrees to apply with
    ``cv2.getRotationMatrix2D``, or ``0.0`` when there are no dark pixels.
    """
    gray = np.asarray(img.convert("L"))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(binary)
    if coords is None:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    return -angle


def prepare_with_angle(img: Image.Image, target_dpi: int = 300) -> tuple[Image.Image, float]:
    """Preprocess a page image for OCR and return the applied deskew angle.

    Pipeline: grayscale -> upscale (so the shorter side reaches ``target_dpi``
    when smaller) -> deskew -> adaptive (OTSU) binarization.

    Returns ``(image, angle)`` where ``image`` is a ``"L"`` PIL image and
    ``angle`` is the deskew rotation applied in degrees (``0.0`` when the image
    was not rotated). The rotation is performed about ``(width // 2,
    height // 2)`` of the upscaled image, which is the prepared image's own
    center.
    """
    gray = img.convert("L")

    # Upscale so the shorter side reaches target_dpi, if currently smaller.
    min_side = min(gray.size)
    if min_side < target_dpi:
        scale = target_dpi / min_side
        new_size = (int(round(gray.width * scale)), int(round(gray.height * scale)))
        gray = gray.resize(new_size, Image.LANCZOS)

    # Deskew.
    angle = deskew_angle(gray)
    if abs(angle) > 0.1:
        arr = np.asarray(gray)
        height, width = arr.shape[:2]
        center = (width // 2, height // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            arr,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        gray = Image.fromarray(rotated)
    else:
        angle = 0.0

    # Adaptive (OTSU) binarization.
    arr = np.asarray(gray)
    _, binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(binary), angle


def prepare(img: Image.Image, target_dpi: int = 300) -> Image.Image:
    """Preprocess a page image for OCR.

    Pipeline: grayscale -> upscale (so the shorter side reaches ``target_dpi``
    when smaller) -> deskew -> adaptive (OTSU) binarization.
    Returns a ``"L"`` PIL image.
    """
    prepared, _ = prepare_with_angle(img, target_dpi)
    return prepared


def rotate_expand(img: Image.Image, angle_cw: int) -> Image.Image:
    """Rotate ``img`` clockwise by a multiple of 90 degrees.

    Uses exact PIL transposes (no interpolation loss), which is what the
    OSD-based ``detect_orientation`` always returns. ``angle_cw`` of 0
    returns the image unchanged.
    """
    transposes = {
        90: Image.Transpose.ROTATE_270,   # 270° CCW == 90° CW
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }
    transpose = transposes.get(angle_cw % 360)
    if transpose is None:
        return img
    return img.transpose(transpose)


def invert_orientation(
    bbox: tuple[float, float, float, float],
    angle_cw: int,
    rotated_size: tuple[int, int],
    original_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Invert the clockwise ``angle_cw`` rotation for an OCR word box.

    Maps the box's four corners from the rotated-image pixel space back to
    the original-image pixel space, undoing the exact (transpose) rotation
    that :func:`rotate_expand` applied. ``rotated_size`` is ``(width,
    height)`` of the image after rotation, ``original_size`` before it.

    In y-down image coordinates a visual clockwise rotation by ``angle``
    is the matrix ``[[cos, -sin], [sin, cos]]`` about the centers with
    canvas expansion, so the inverse maps each corner ``p'`` as
    ``p = c1 + R(-angle) * (p' - c2)`` where ``c1``/``c2`` are the centers
    of the original and rotated images.
    """
    cx1 = original_size[0] / 2
    cy1 = original_size[1] / 2
    cx2 = rotated_size[0] / 2
    cy2 = rotated_size[1] / 2
    theta = math.radians(angle_cw % 360)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    x0, y0, x1, y1 = bbox
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        dx = x - cx2
        dy = y - cy2
        # R(-angle): [[cos, sin], [-sin, cos]] in y-down coordinates.
        xs.append(cx1 + dx * cos_t + dy * sin_t)
        ys.append(cy1 - dx * sin_t + dy * cos_t)
    return min(xs), min(ys), max(xs), max(ys)


def invert_rotation_deskew(
    bbox: tuple[float, float, float, float],
    *,
    angle: float,
    prepared_size: tuple[int, int],
    rot: int,
    oriented_size: tuple[int, int],
    rendered_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Map a box from prepared-image pixels back to rendered-image pixels.

    Undoes, in order: the deskew rotation (:func:`invert_deskew`, when
    ``angle`` is nonzero), the upscale made by :func:`prepare_with_angle`
    (a linear rescale from ``prepared_size`` to ``oriented_size``), and the
    orientation rotation (:func:`invert_orientation`, when ``rot`` is
    nonzero). ``rot`` is the clockwise orientation angle; ``oriented_size``
    is the size of the image between orientation rotation and deskew.
    """
    x0, y0, x1, y1 = bbox
    if angle:
        x0, y0, x1, y1 = invert_deskew(x0, y0, x1, y1, angle, prepared_size)
    if rot:
        scale_x = oriented_size[0] / prepared_size[0]
        scale_y = oriented_size[1] / prepared_size[1]
        x0, y0, x1, y1 = x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y
        x0, y0, x1, y1 = invert_orientation(
            (x0, y0, x1, y1), rot, oriented_size, rendered_size)
    return x0, y0, x1, y1


def invert_deskew(
    x0: float, y0: float, x1: float, y1: float, angle: float, size: tuple[int, int]
) -> tuple[float, float, float, float]:
    """Invert the deskew rotation applied by ``prepare`` for an OCR word box.

    Maps the box's four corners from prepared-image pixel space back through a
    rotation of ``-angle`` degrees about the deskew center (``size[0] // 2``,
    ``size[1] // 2`` — the convention used by ``prepare_with_angle``) and
    returns the axis-aligned bounding box ``(x0, y0, x1, y1)`` in the
    pre-deskew coordinate frame. ``size`` is ``(width, height)`` of the
    prepared image.
    """
    cx = size[0] // 2
    cy = size[1] // 2
    theta = math.radians(angle)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        dx = x - cx
        dy = y - cy
        xs.append(cx + dx * cos_t - dy * sin_t)
        ys.append(cy + dx * sin_t + dy * cos_t)
    return min(xs), min(ys), max(xs), max(ys)
