from __future__ import annotations

import math
import os
import re
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from hashlib import sha256
from typing import Any

from .aliases import alias_tokens
from .models import AtomRecord, RetrievalResult, RetrievedAtom
from .store import InMemoryAtomStore

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_STOPWORDS = {
    "是",
    "什",
    "么",
    "的",
    "了",
    "在",
    "哪",
    "里",
    "看",
    "有",
    "没",
    "吗",
    "和",
    "与",
    "或",
}


class _Bm25Index:
    """BM25-lite 用的倒排索引,按语料构建一次、各次查询复用。

    每次查询都重新分词整个语料是 O(corpus),monorepo 上线后(约 17.6 万个原子)
    单次查询要 50 秒左右,延迟主要就耗在这。改成对每个 sub_kbs 切片只建一次倒排索引,
    单次查询就降到 O(query_terms × postings)。结果和之前全量扫描打分完全一致
    (idf/avg_len 相同,分数相同,同分按原子列表顺序排)。"""

    __slots__ = ("atoms", "postings", "doc_len", "doc_freq", "avg_len", "total_docs")

    def __init__(self, atoms, postings, doc_len, doc_freq, avg_len, total_docs) -> None:
        self.atoms = atoms
        self.postings = postings
        self.doc_len = doc_len
        self.doc_freq = doc_freq
        self.avg_len = avg_len
        self.total_docs = total_docs

    @classmethod
    def build(cls, atoms, aliases) -> "_Bm25Index":
        postings: dict[str, list[tuple[int, int]]] = {}
        doc_len: list[int] = []
        doc_freq: Counter[str] = Counter()
        raw_total = 0
        for idx, atom in enumerate(atoms):
            tokens = tokenize(_search_text(atom), aliases=aliases)
            raw_total += len(tokens)
            doc_len.append(len(tokens) or 1)
            tf = Counter(tokens)
            for term, count in tf.items():
                postings.setdefault(term, []).append((idx, count))
            doc_freq.update(tf.keys())
        total = len(atoms)
        avg_len = (raw_total / total) if total else 0.0
        return cls(list(atoms), postings, doc_len, doc_freq, avg_len, total)


class Bm25LiteRetriever:
    def __init__(
        self,
        store: InMemoryAtomStore,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.store = store
        self.k1 = k1
        self.b = b
        self.aliases = aliases or {}
        self._index_cache: dict[Any, _Bm25Index] = {}

    def _index(self, sub_kbs: set[str] | None) -> _Bm25Index:
        key = frozenset(sub_kbs) if sub_kbs else None
        index = self._index_cache.get(key)
        if index is None:
            index = _Bm25Index.build(self.store.list_atoms(sub_kbs=sub_kbs), self.aliases)
            self._index_cache[key] = index
        return index

    def retrieve(self, query: str, *, sub_kbs: set[str] | None = None, top_k: int = 4) -> RetrievalResult:
        index = self._index(sub_kbs)
        if not index.atoms:
            return RetrievalResult(query=query, top_atoms=())

        query_terms = tuple(dict.fromkeys(tokenize(query, aliases=self.aliases)))
        if not query_terms:
            return RetrievalResult(query=query, top_atoms=())

        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        for term in query_terms:
            postings = index.postings.get(term)
            if not postings:
                continue
            df = index.doc_freq.get(term, 0)
            idf = math.log(1 + (index.total_docs - df + 0.5) / (df + 0.5))
            for idx, freq in postings:
                doc_len = index.doc_len[idx]
                denom = freq + self.k1 * (1 - self.b + self.b * doc_len / max(index.avg_len, 1))
                scores[idx] = scores.get(idx, 0.0) + idf * (freq * (self.k1 + 1) / denom)
                matched.setdefault(idx, []).append(term)

        if not scores:
            return RetrievalResult(query=query, top_atoms=())

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        results = [
            RetrievedAtom(atom=index.atoms[idx], score=score, matched_terms=tuple(matched[idx]))
            for idx, score in ranked
        ]
        top_atoms = tuple(results[:top_k])
        return RetrievalResult(
            query=query,
            top_atoms=top_atoms,
            sparse_hits=tuple(item.atom.atom_id for item in results[:20]),
            rrf_top20=tuple(item.atom.atom_id for item in results[:20]),
            rerank_hits=tuple(item.atom.atom_id for item in top_atoms),
            retriever="bm25-lite",
        )

    def _score_terms(
        self,
        query_terms: tuple[str, ...],
        terms: list[str],
        doc_freq: Counter[str],
        total_docs: int,
        avg_len: float,
    ) -> tuple[float, list[str]]:
        counts = Counter(terms)
        doc_len = len(terms) or 1
        score = 0.0
        matched: list[str] = []

        for term in query_terms:
            freq = counts[term]
            if freq == 0:
                continue
            matched.append(term)
            idf = math.log(1 + (total_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            denom = freq + self.k1 * (1 - self.b + self.b * doc_len / max(avg_len, 1))
            score += idf * (freq * (self.k1 + 1) / denom)

        return score, matched


_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+|[0-9]+")


def _split_identifier(token: str) -> list[str]:
    """拆分代码标识符的子词元:getUserName/device_seq -> get,user,name / device,seq。

    纯小写单词和纯中文文本不会拆出任何子词元;任何位置(包括正文)出现的标识符
    都会额外拆出子词元,原词元始终保留,所以这只会提升召回,绝不会丢掉整个标识符。
    """
    if "_" not in token and token == token.lower():
        return []
    low = token.lower()
    out: list[str] = []
    for chunk in token.split("_"):
        for match in _CAMEL_RE.finditer(chunk):
            part = match.group(0).lower()
            if len(part) > 1 and part != low and part not in out:
                out.append(part)
    return out


def tokenize(text: str, *, aliases: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        raw = match.group(0)
        low = raw.lower()
        if low not in _STOPWORDS:
            tokens.append(low)
        for sub in _split_identifier(raw):
            if sub not in _STOPWORDS:
                tokens.append(sub)
    if aliases:
        tokens.extend(alias_tokens(text, aliases))
    return tokens


def _search_text(atom: AtomRecord) -> str:
    draft = atom.draft
    return "\n".join([draft.contextual_prefix, draft.text, draft.source_title, " ".join(draft.section_path)])


def _document_frequency(token_lists) -> Counter[str]:
    freq: Counter[str] = Counter()
    for tokens in token_lists:
        freq.update(set(tokens))
    return freq


class HybridLiteRetriever:
    """确定性的 P1 混合检索基线:BM25-lite + 哈希词法向量 + RRF。"""

    def __init__(
        self,
        store: InMemoryAtomStore,
        *,
        aliases: dict[str, tuple[str, ...]] | None = None,
        dense_top_k: int = 30,
        sparse_top_k: int = 30,
        rrf_k: int = 60,
        reranker: Any | None = None,
    ) -> None:
        self.store = store
        self.aliases = aliases or {}
        self.dense_top_k = dense_top_k
        self.sparse_top_k = sparse_top_k
        self.rrf_k = rrf_k
        self.reranker = reranker
        self.sparse = Bm25LiteRetriever(store, aliases=self.aliases)

    def retrieve(self, query: str, *, sub_kbs: set[str] | None = None, top_k: int = 4) -> RetrievalResult:
        atoms = self.store.list_atoms(sub_kbs=sub_kbs)
        if not atoms:
            return RetrievalResult(query=query, top_atoms=(), retriever="hybrid-lite")

        sparse = self.sparse.retrieve(query, sub_kbs=sub_kbs, top_k=self.sparse_top_k)
        dense_hits = _dense_rank(query, atoms, aliases=self.aliases)[: self.dense_top_k]
        if not sparse.top_atoms and not dense_hits:
            return RetrievalResult(query=query, top_atoms=(), retriever="hybrid-lite")

        sparse_by_id = {hit.atom.atom_id: hit for hit in sparse.top_atoms}
        atom_by_id = {atom.atom_id: atom for atom in atoms}
        scores: dict[str, float] = {}
        for rank, hit in enumerate(sparse.top_atoms, start=1):
            scores[hit.atom.atom_id] = scores.get(hit.atom.atom_id, 0.0) + 1.0 / (self.rrf_k + rank)
        for rank, atom_id in enumerate(dense_hits, start=1):
            scores[atom_id] = scores.get(atom_id, 0.0) + 1.0 / (self.rrf_k + rank)

        ranked_ids = tuple(atom_id for atom_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True))
        reranked_ids = _rerank_candidates(
            query, ranked_ids[:20], atom_by_id, aliases=self.aliases, reranker=self.reranker
        )
        top_ids = reranked_ids[:top_k]
        top_atoms: list[RetrievedAtom] = []
        for atom_id in top_ids:
            sparse_hit = sparse_by_id.get(atom_id)
            atom = atom_by_id[atom_id]
            matched_terms = sparse_hit.matched_terms if sparse_hit else tuple(_matched_terms(query, atom, self.aliases))
            top_atoms.append(RetrievedAtom(atom=atom, score=scores[atom_id], matched_terms=matched_terms))

        return RetrievalResult(
            query=query,
            top_atoms=tuple(top_atoms),
            sparse_hits=tuple(hit.atom.atom_id for hit in sparse.top_atoms),
            dense_hits=tuple(dense_hits),
            rrf_top20=ranked_ids[:20],
            rerank_hits=tuple(top_ids),
            retriever="hybrid-lite",
        )


class QdrantLiteRetriever:
    """基于 Qdrant 的向量检索器,用的向量和 export-index 一样是确定性词法向量。"""

    def __init__(
        self,
        store: InMemoryAtomStore,
        *,
        url: str,
        api_key: str = "",
        collection: str = "codekb_atoms",
        timeout_seconds: int = 3,
        candidate_top_k: int = 20,
        aliases: dict[str, tuple[str, ...]] | None = None,
        embedder: "Embedder | None" = None,
        enable_bm25_fallback: bool = True,
        reranker: Any | None = None,
    ) -> None:
        if not url:
            raise ValueError("Qdrant url is required for qdrant-lite retriever")
        if not collection:
            raise ValueError("Qdrant collection is required for qdrant-lite retriever")
        self.store = store
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.collection = collection
        self.timeout_seconds = timeout_seconds
        self.candidate_top_k = candidate_top_k
        self.aliases = aliases or {}
        if embedder is None:
            # 惰性导入,避免 embedding -> retrieval 的循环导入。
            from .embedding_config import resolve_embedder

            embedder = resolve_embedder(aliases=self.aliases)
        self.embedder = embedder
        self.enable_bm25_fallback = enable_bm25_fallback
        self.reranker = reranker

    def retrieve(self, query: str, *, sub_kbs: set[str] | None = None, top_k: int = 4) -> RetrievalResult:
        # 向量化(比如调远程服务)可能因瞬时故障抛错。这里同样走 BM25 兜底(B8),
        # 不让它往上抛而绕过安全网。
        try:
            vector = self.embedder.embed_query(query)
        except RuntimeError:
            return self._empty_or_fallback(query, sub_kbs=sub_kbs, top_k=top_k)
        if not any(vector):
            return self._empty_or_fallback(query, sub_kbs=sub_kbs, top_k=top_k)

        payload: dict[str, Any] = {
            "query": vector,
            "limit": max(top_k, self.candidate_top_k),
            "with_payload": True,
        }
        if sub_kbs:
            payload["filter"] = {"must": [{"key": "sub_kb_id", "match": {"any": sorted(sub_kbs)}}]}

        try:
            points = self._query_points(payload)
        except RuntimeError:
            return self._empty_or_fallback(query, sub_kbs=sub_kbs, top_k=top_k)
        atom_by_id: dict[str, AtomRecord] = {}
        score_by_id: dict[str, float] = {}
        dense_hits: list[str] = []
        for point in points:
            atom_id = str(point.get("id", ""))
            if not atom_id:
                continue
            dense_hits.append(atom_id)
            try:
                atom = self.store.get(atom_id)
            except KeyError:
                continue
            atom_by_id[atom_id] = atom
            score_by_id[atom_id] = float(point.get("score") or 0.0)

        reranked_ids = _rerank_candidates(
            query, tuple(atom_by_id.keys()), atom_by_id, aliases=self.aliases, reranker=self.reranker
        )
        hits = [
            RetrievedAtom(
                atom=atom_by_id[atom_id],
                score=score_by_id.get(atom_id, 0.0),
                matched_terms=tuple(_matched_terms(query, atom_by_id[atom_id], self.aliases)),
            )
            for atom_id in reranked_ids[:top_k]
        ]

        if not hits:
            return self._empty_or_fallback(query, sub_kbs=sub_kbs, top_k=top_k)

        return _with_fallback(
            RetrievalResult(
                query=query,
                top_atoms=tuple(hits),
                dense_hits=tuple(dense_hits[:20]),
                rrf_top20=tuple(dense_hits[:20]),
                rerank_hits=tuple(hit.atom.atom_id for hit in hits),
                retriever="qdrant-lite",
            ),
            "",
        )

    def _empty_or_fallback(
        self,
        query: str,
        *,
        sub_kbs: set[str] | None,
        top_k: int,
    ) -> RetrievalResult:
        """Qdrant 没返回结果时,兜底走 BM25(B8)。

        查询向量为空、请求失败或命中数为零时触发。retriever 标签仍保持 ``qdrant-lite``,
        让调用方看到的是配置的模式;此时 ``dense_hits`` 为空,并附上 ``fallback='bm25'``
        标记。``enable_bm25_fallback`` 为 False 时关闭此兜底。
        """

        if not self.enable_bm25_fallback:
            return _with_fallback(
                RetrievalResult(query=query, top_atoms=(), retriever="qdrant-lite"),
                "",
            )
        sparse = Bm25LiteRetriever(self.store, aliases=self.aliases).retrieve(
            query, sub_kbs=sub_kbs, top_k=top_k
        )
        return _with_fallback(
            RetrievalResult(
                query=query,
                top_atoms=sparse.top_atoms,
                sparse_hits=sparse.sparse_hits,
                dense_hits=(),
                rrf_top20=sparse.rrf_top20,
                rerank_hits=tuple(hit.atom.atom_id for hit in sparse.top_atoms),
                retriever="qdrant-lite",
            ),
            "bm25",
        )

    def _query_points(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        endpoint = (
            f"{self.url}/collections/{urllib.parse.quote(self.collection, safe='')}/points/query"
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={**_qdrant_headers(self.api_key), "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Qdrant retrieval request failed") from exc
        result = data.get("result", {})
        if isinstance(result, dict) and isinstance(result.get("points"), list):
            return [point for point in result["points"] if isinstance(point, dict)]
        if isinstance(result, list):
            return [point for point in result if isinstance(point, dict)]
        return []


class QdrantHybridLiteRetriever:
    """Qdrant 向量候选 + BM25 精确候选,经 RRF 融合后本地重排。"""

    def __init__(
        self,
        store: InMemoryAtomStore,
        *,
        url: str,
        api_key: str = "",
        collection: str = "codekb_atoms",
        timeout_seconds: int = 3,
        dense_top_k: int = 20,
        sparse_top_k: int = 20,
        rrf_k: int = 60,
        aliases: dict[str, tuple[str, ...]] | None = None,
        embedder: "Embedder | None" = None,
        reranker: Any | None = None,
    ) -> None:
        self.store = store
        self.aliases = aliases or {}
        self.reranker = reranker
        self.dense_top_k = dense_top_k
        self.sparse_top_k = sparse_top_k
        self.rrf_k = rrf_k
        self.dense = QdrantLiteRetriever(
            store,
            url=url,
            api_key=api_key,
            collection=collection,
            timeout_seconds=timeout_seconds,
            candidate_top_k=dense_top_k,
            aliases=self.aliases,
            embedder=embedder,
            # 混合检索器自己已经提供了 BM25 精确候选,内层向量检索器不能再走兜底重复计入。
            enable_bm25_fallback=False,
        )
        self.sparse = Bm25LiteRetriever(store, aliases=self.aliases)

    def retrieve(self, query: str, *, sub_kbs: set[str] | None = None, top_k: int = 4) -> RetrievalResult:
        dense = self.dense.retrieve(query, sub_kbs=sub_kbs, top_k=self.dense_top_k)
        sparse = self.sparse.retrieve(query, sub_kbs=sub_kbs, top_k=self.sparse_top_k)
        if not dense.top_atoms and not sparse.top_atoms:
            return RetrievalResult(query=query, top_atoms=(), retriever="qdrant-hybrid-lite")

        atom_by_id: dict[str, AtomRecord] = {}
        sparse_by_id: dict[str, RetrievedAtom] = {}
        scores: dict[str, float] = {}
        for rank, hit in enumerate(sparse.top_atoms, start=1):
            atom_id = hit.atom.atom_id
            atom_by_id[atom_id] = hit.atom
            sparse_by_id[atom_id] = hit
            scores[atom_id] = scores.get(atom_id, 0.0) + 1.0 / (self.rrf_k + rank)
        for rank, hit in enumerate(dense.top_atoms, start=1):
            atom_id = hit.atom.atom_id
            atom_by_id[atom_id] = hit.atom
            scores[atom_id] = scores.get(atom_id, 0.0) + 1.0 / (self.rrf_k + rank)

        ranked_ids = tuple(atom_id for atom_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True))
        reranked_ids = _rerank_candidates(
            query, ranked_ids[:20], atom_by_id, aliases=self.aliases, reranker=self.reranker
        )
        top_ids = reranked_ids[:top_k]
        top_atoms = []
        for atom_id in top_ids:
            sparse_hit = sparse_by_id.get(atom_id)
            atom = atom_by_id[atom_id]
            top_atoms.append(
                RetrievedAtom(
                    atom=atom,
                    score=scores[atom_id],
                    matched_terms=sparse_hit.matched_terms if sparse_hit else tuple(_matched_terms(query, atom, self.aliases)),
                )
            )

        return RetrievalResult(
            query=query,
            top_atoms=tuple(top_atoms),
            sparse_hits=tuple(hit.atom.atom_id for hit in sparse.top_atoms),
            dense_hits=dense.dense_hits,
            rrf_top20=ranked_ids[:20],
            rerank_hits=tuple(top_ids),
            retriever="qdrant-hybrid-lite",
        )


# cross-encoder 重排实际打分的候选数(取 RRF 排序的前几个)。
# cross-encoder 开销和候选数成正比,在 CPU 上靠这个值给 /ask 延迟封顶。
# 其余候选保持 RRF 顺序,接在重排头部之后。
_RERANK_CANDIDATES = max(1, int(os.environ.get("CODEKB_RERANK_CANDIDATES", "12") or "12"))


def _rerank_candidates(
    query: str,
    atom_ids,
    atom_by_id: dict[str, AtomRecord],
    *,
    aliases: dict[str, tuple[str, ...]],
    reranker: Any | None = None,
) -> tuple[str, ...]:
    """对候选原子 id 重排。

    配置了真正的 cross-encoder ``reranker`` 就用它,否则退回确定性的词元重合度启发式
    (``_rerank_ids``)。只有 RRF 排序靠前的 ``_RERANK_CANDIDATES`` 个 id 会交给
    cross-encoder 打分(给 CPU 延迟封顶),其余保持 RRF 顺序。重排出错则退回启发式,
    保证检索绝不崩。
    """
    ids = tuple(atom_ids)
    if reranker is not None and len(ids) > 1:
        head = ids[:_RERANK_CANDIDATES]
        tail = ids[_RERANK_CANDIDATES:]
        docs = [_search_text(atom_by_id[i]) for i in head]
        try:
            order = reranker.rerank(query, docs)
        except Exception:  # noqa: BLE001 - 绝不让重排把检索带崩
            order = None
        if order:
            ranked = [head[idx] for idx, _ in order if 0 <= idx < len(head)]
            seen = set(ranked)
            ranked.extend(i for i in head if i not in seen)
            ranked.extend(tail)  # 未打分的尾部保持 RRF 顺序
            return tuple(ranked)
    return _rerank_ids(query, ids, atom_by_id, aliases=aliases)


def _dense_rank(
    query: str,
    atoms: list[AtomRecord],
    *,
    aliases: dict[str, tuple[str, ...]],
) -> list[str]:
    query_vector = Counter(tokenize(query, aliases=aliases))
    if not query_vector:
        return []
    scored: list[tuple[str, float]] = []
    for atom in atoms:
        score = _cosine(query_vector, Counter(tokenize(_search_text(atom), aliases=aliases)))
        if score > 0:
            scored.append((atom.atom_id, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [atom_id for atom_id, _ in scored]


def hashed_lexical_vector(
    text: str,
    *,
    dimensions: int = 64,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text, aliases=aliases):
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def _with_fallback(result: RetrievalResult, fallback: str) -> RetrievalResult:
    """给(冻结的)RetrievalResult 附上 ``fallback`` 标记。

    RetrievalResult 是冻结 dataclass,没有声明 ``fallback`` 字段(加字段超出本 PR 的
    改动范围),所以用 ``object.__setattr__`` 写入。调用方按 ``result.fallback`` 读取。
    """

    object.__setattr__(result, "fallback", fallback)
    return result


def _qdrant_headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    return headers


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    numerator = sum(left[token] * right[token] for token in left.keys() & right.keys())
    if numerator == 0:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / max(left_norm * right_norm, 1e-9)


def _rerank_ids(
    query: str,
    atom_ids: tuple[str, ...],
    atom_by_id: dict[str, AtomRecord],
    *,
    aliases: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    query_terms = set(tokenize(query, aliases=aliases))
    scored: list[tuple[str, int, int]] = []
    for rank, atom_id in enumerate(atom_ids):
        atom = atom_by_id[atom_id]
        overlap = len(query_terms.intersection(tokenize(_search_text(atom), aliases=aliases)))
        scored.append((atom_id, overlap, -rank))
    scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return tuple(atom_id for atom_id, _, _ in scored)


def _matched_terms(
    query: str,
    atom: AtomRecord,
    aliases: dict[str, tuple[str, ...]],
) -> list[str]:
    terms = set(tokenize(_search_text(atom), aliases=aliases))
    return [token for token in dict.fromkeys(tokenize(query, aliases=aliases)) if token in terms]
