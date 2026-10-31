"""Generate original, vector-style Cloud Quota Guard brand images."""

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components/quota_guard/brand"
BRAND.mkdir(exist_ok=True)
image = Image.new("RGBA", (1024, 1024))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, 1023, 1023), radius=224, fill="#142438")
shield = [
    (512, 100),
    (866, 244),
    (836, 590),
    (776, 722),
    (666, 830),
    (512, 924),
    (358, 830),
    (248, 722),
    (188, 590),
    (158, 244),
]
draw.polygon(shield, fill="#83ddcc")
inner = [
    (512, 158),
    (808, 279),
    (780, 576),
    (725, 692),
    (628, 787),
    (512, 860),
    (396, 787),
    (299, 692),
    (244, 576),
    (216, 279),
]
draw.polygon(inner, fill="#142438")
# Cloud and three utilization bars: the product's two core ideas.
draw.ellipse((280, 323, 454, 497), fill="#eaf5f6")
draw.ellipse((373, 245, 617, 489), fill="#eaf5f6")
draw.ellipse((554, 332, 742, 520), fill="#eaf5f6")
draw.rounded_rectangle((293, 399, 722, 529), radius=60, fill="#eaf5f6")
for box in [(337, 616, 419, 720), (471, 570, 553, 720), (605, 524, 687, 720)]:
    draw.rounded_rectangle(box, radius=20, fill="#83ddcc")
for size, name in [(256, "icon.png"), (512, "icon@2x.png")]:
    image.resize((size, size), Image.Resampling.LANCZOS).save(
        BRAND / name, optimize=True
    )
print("Created 256px and 512px RGBA brand icons")
