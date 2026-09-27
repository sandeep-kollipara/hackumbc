from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundingBox(StrictModel):
    """Coordinates normalized to [0, 1000], independent of image size."""
    x1: int = Field(ge=0, le=1000)
    y1: int = Field(ge=0, le=1000)
    x2: int = Field(ge=0, le=1000)
    y2: int = Field(ge=0, le=1000)

    @model_validator(mode="after")
    def ordered(self):
        if self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError("Bounding boxes require x1 < x2 and y1 < y2")
        return self


class SceneMeta(StrictModel):
    description: str
    environment: str
    lighting: str


class DetectedObject(StrictModel):
    object_name: str = Field(min_length=1)
    object_description: str
    object_location: str
    bounding_boxes: list[BoundingBox] = Field(min_length=1)


class ImageAnalysis(StrictModel):
    scene_meta: SceneMeta
    objects: list[DetectedObject]
