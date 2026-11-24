"""Small dependency-free retriever for product knowledge documents."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path


def _terms(text: str) -> list[str]:
    """Tokenize Chinese with character bigrams and retain Latin/numeric terms."""
    normalized = re.sub(r"\s+", "", text.lower())
    terms = re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)*", normalized)
    for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
        terms.extend(run[i:i + 2] for i in range(max(1, len(run) - 1)))
        if len(run) == 1:
            terms.append(run)
    return terms


def retrieve(product_id: str, query: str, knowledge_file: Path, limit: int = 3) -> list[dict]:
    data = json.loads(knowledge_file.read_text(encoding="utf-8"))
    docs = [item for item in data.get("documents", []) if item.get("product_id") == product_id]
    query_terms = _terms(query)
    if not docs or not query_terms:
        return []

    doc_terms = [_terms(" ".join([d.get("title", ""), d.get("content", ""), " ".join(d.get("tags", []))])) for d in docs]
    df = Counter(term for terms in doc_terms for term in set(terms))
    query_counts = Counter(query_terms)
    scored = []
    for doc, terms in zip(docs, doc_terms):
        counts = Counter(terms)
        score = 0.0
        for term, q_count in query_counts.items():
            frequency = counts.get(term, 0)
            if frequency:
                idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * (frequency * 2.2) / (frequency + 1.2) * min(q_count, 2)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {**doc, "score": round(score, 3)}
        for score, doc in scored[:limit]
    ]
