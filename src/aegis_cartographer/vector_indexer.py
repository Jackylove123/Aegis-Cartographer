import os
import json
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from typing import List, Dict, Any


class AegisVectorIndex:
    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.index_file = os.path.join(storage_path, "vector_index.json")
        self.nodes_data = []
        self.vectorizer = TfidfVectorizer()
        self.load()

    def load(self):
        if os.path.exists(self.index_file):
            with open(self.index_file, 'r', encoding='utf-8') as f:
                self.nodes_data = json.load(f)

    def save(self):
        os.makedirs(self.storage_path, exist_ok=True)
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(self.nodes_data, f, ensure_ascii=False, indent=2)

    def upsert_node_index(self, state_id: str, context: str, metadata: Dict[str, Any]):
        self.nodes_data = [n for n in self.nodes_data if n['id'] != state_id]
        self.nodes_data.append({"id": state_id, "text": context, "metadata": metadata})
        self.save()

    def search_semantic(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        if not self.nodes_data:
            return []
        texts = [n['text'] for n in self.nodes_data]
        tfidf_matrix = self.vectorizer.fit_transform(texts)
        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()
        top_indices = similarities.argsort()[-n_results:][::-1]
        return [self.nodes_data[idx]['metadata'] for idx in top_indices if similarities[idx] > 0]
