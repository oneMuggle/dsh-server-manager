from pydantic import BaseModel


class DirectoryListing(BaseModel):
    directory: str
    directories: list[str]
    truncated: bool
