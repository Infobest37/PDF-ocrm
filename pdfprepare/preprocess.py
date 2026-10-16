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


def prepare(img: Image.Image, target_dpi: int = 300) -> Image.Image:
    """Preprocess a page image for OCR.

    Pipeline: grayscale -> upscale (so the shorter side reaches ``target_dpi``
    when smaller) -> deskew -> adaptive (OTSU) binarization.
    Returns a ``"L"`` PIL image.
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

    # Adaptive (OTSU) binarization.
    arr = np.asarray(gray)
    _, binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(binary)
