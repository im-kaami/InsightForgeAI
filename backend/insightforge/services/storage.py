import shutil
from pathlib import Path


class Storage:
    def __init__(self, root: Path):
        self.root = root

    def dataset_dir(self, user_id: str, dataset_id: str) -> Path:
        path = self.root / "users" / user_id / "datasets" / dataset_id
        for child in (path / "uploads", path / "downloads"):
            child.mkdir(parents=True, exist_ok=True)
        return path

    def run_dir(self, user_id: str, run_id: str) -> Path:
        path = self.root / "users" / user_id / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def delete_dataset(self, user_id: str, dataset_id: str) -> None:
        path = self.root / "users" / user_id / "datasets" / dataset_id
        if path.is_dir():
            shutil.rmtree(path)
