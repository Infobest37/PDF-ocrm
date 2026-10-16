import pytest

from pdfprepare.preprocess import deskew_angle, invert_deskew, prepare


def test_prepare_returns_grayscale_and_upscales():
    from PIL import Image
    src = Image.new("RGB", (300, 400), "white")
    out = prepare(src, target_dpi=300)
    assert out.mode == "L"
    assert out.size[0] >= 300 and out.size[1] >= 400


def test_deskew_angle_level_image_is_zero():
    from PIL import Image
    img = Image.new("L", (200, 200), 255)
    assert abs(deskew_angle(img)) < 0.1


def test_invert_deskew_round_trip():
    import math

    page_rect = (0.0, 0.0, 612.0, 792.0)
    prepared_size = (1000, 1300)
    angle = 3.0
    cx, cy = prepared_size[0] // 2, prepared_size[1] // 2
    theta = math.radians(angle)

    for px, py in [(72, 72), (300, 400), (0, 0), (612, 792), (150.5, 200.25)]:
        # Forward transform (exactly as prepare does): page point -> prepared
        # pixel, then rotate by ``angle`` about the deskew center.
        x = px * prepared_size[0] / page_rect[2]
        y = py * prepared_size[1] / page_rect[3]
        xr = cx + (x - cx) * math.cos(theta) + (y - cy) * math.sin(theta)
        yr = cy - (x - cx) * math.sin(theta) + (y - cy) * math.cos(theta)

        # Inverse: un-rotate (as a degenerate point box), then scale back.
        bx0, by0, _, _ = invert_deskew(xr, yr, xr, yr, angle, prepared_size)
        back_px = bx0 * page_rect[2] / prepared_size[0]
        back_py = by0 * page_rect[3] / prepared_size[1]

        assert back_px == pytest.approx(px, abs=1e-6)
        assert back_py == pytest.approx(py, abs=1e-6)


def test_invert_deskew_box_bbox_is_sensible():
    import math

    prepared_size = (1000, 1300)
    angle = 30.0

    x0, y0, x1, y1 = invert_deskew(520, 640, 540, 660, angle, prepared_size)

    # A 20x20 box rotated 30 deg has an axis-aligned bbox of side
    # 20*(cos + sin).
    expected = 20 * (math.cos(math.radians(angle)) + math.sin(math.radians(angle)))
    assert x1 - x0 == pytest.approx(expected, abs=0.5)
    assert y1 - y0 == pytest.approx(expected, abs=0.5)
