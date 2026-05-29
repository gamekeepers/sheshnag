from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class JobCreate(BaseModel):
    pass

class JobOut(BaseModel):
    id:          str
    status:      str
    input_path:  Optional[str]
    output_path: Optional[str]
    created_at:  datetime

    class Config:
        from_attributes = True