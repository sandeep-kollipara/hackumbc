from dataclasses import dataclass
import math
from pathlib import Path
from PIL import Image


@dataclass
class Crop:
    index: str
    path: Path
    image: Image.Image
    metadata: dict


def crop_objects(image, analysis, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    width, height = image.size
    for object_index, obj in enumerate(analysis.objects):
        for box_index, box in enumerate(obj.bounding_boxes):
            pixels = (math.floor(width * box.x1 / 1000), math.floor(height * box.y1 / 1000),
                      math.ceil(width * box.x2 / 1000), math.ceil(height * box.y2 / 1000))
            index = f"{object_index:04d}_{box_index:03d}"
            path = output_dir / f"object_{index}.jpg"
            cropped = image.crop(pixels)
            cropped.save(path, quality=95)
            yield Crop(index, path, cropped, {
                **obj.model_dump(), "crop_path": str(path.resolve()),
                "scene_meta": analysis.scene_meta.model_dump(),
                "crop_box": box.model_dump(), "crop_pixel_box": list(pixels),
                "object_index": object_index, "box_index": box_index,
                "image_width": width, "image_height": height,
            })
