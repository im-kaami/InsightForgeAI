# memory.py
from typing import List, Dict, Any, Optional

class SimpleMemory:
    def __init__(self):
        self.docs: List[Dict[str,Any]] = []

    def add(self, doc_id: str, text: str, metadata: Optional[Dict[str,Any]] = None):
        self.docs.append({"id": doc_id, "text": text, "meta": metadata or {}})

    def query_recent(self, k: int = 3):
        return self.docs[-k:][::-1]
