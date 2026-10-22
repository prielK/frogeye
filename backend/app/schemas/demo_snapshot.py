from datetime import datetime

from pydantic import BaseModel, Field


class DemoSnapshotOut(BaseModel):
    captured_at: datetime | None = Field(
        description="When the demo data this database started from was captured; null when "
        "the database did not start from the bundled demo snapshot."
    )
