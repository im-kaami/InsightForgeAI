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

    def temp_dir(self) -> Path:
        path = self.root / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_upload_path(
        self,
        user_id: str,
        dataset_id: str,
        filename: str,
        temp_path: Path,
        version_id: str | None = None,
    ) -> Path:
        directory = (
            self.version_dir(user_id, dataset_id, version_id)
            if version_id
            else self.dataset_dir(user_id, dataset_id)
        )
        destination = directory / "uploads" / Path(filename).name
        destination.unlink(missing_ok=True)
        shutil.move(str(temp_path), destination)
        return destination

    def version_dir(self, user_id: str, dataset_id: str, version_id: str) -> Path:
        path = self.dataset_dir(user_id, dataset_id) / "versions" / version_id
        for child in (path / "uploads", path / "downloads"):
            child.mkdir(parents=True, exist_ok=True)
        return path

    def run_dir(self, user_id: str, run_id: str) -> Path:
        path = self.root / "users" / user_id / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def model_dir(self, user_id: str, model_id: str) -> Path:
        path = self.root / "users" / user_id / "models" / model_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def delete_model(self, user_id: str, model_id: str) -> None:
        path = self.root / "users" / user_id / "models" / model_id
        if path.is_dir():
            shutil.rmtree(path)

    def delete_dataset(self, user_id: str, dataset_id: str) -> None:
        path = self.root / "users" / user_id / "datasets" / dataset_id
        if path.is_dir():
            shutil.rmtree(path)
