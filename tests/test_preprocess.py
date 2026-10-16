from pdfprepare.preprocess import deskew_angle, prepare


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
