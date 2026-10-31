"""Real multilingual embeddings; SQLite is a rebuildable, text-free cache."""
import contextlib
import functools
import hashlib
import json
import math
import os
import pathlib
import sqlite3
import threading
import urllib.parse
import urllib.request

DEFAULT_MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'


class SemanticUnavailable(RuntimeError):
    pass


def unit(vector):
    values = [float(value) for value in vector]
    if not values or len(values) > 4096 or not all(math.isfinite(value) for value in values):
        raise SemanticUnavailable('Invalid embedding dimensions or nonfinite values')
    length = math.sqrt(sum(value * value for value in values))
    if length <= 0:
        raise SemanticUnavailable('Zero embedding vector')
    return [value / length for value in values]


class LocalEncoder:
    def __init__(self, model):
        try:
            from fastembed import TextEmbedding
        except ImportError as error:
            raise SemanticUnavailable('Install portable-memory[semantic] to enable local vector search') from error
        try:
            self.encoder = TextEmbedding(model_name=model, threads=2,
                local_files_only=os.environ.get('MEMORY_LOCAL_FILES_ONLY') == '1',
                cache_dir=os.environ.get('MEMORY_MODEL_CACHE', str(pathlib.Path.home() / '.cache/portable-memory')))
        except Exception as error:
            raise SemanticUnavailable('Local embedding model could not be loaded; check the model cache/network') from error
        self.lock = threading.Lock()
        model_directory = getattr(self.encoder.model, '_model_dir', None)
        if model_directory is None:
            raise SemanticUnavailable('Cannot determine the local model version')
        checksum = hashlib.sha256()
        for file in sorted(pathlib.Path(model_directory).rglob('*')):
            if file.is_file() and (file.suffix == '.onnx' or file.name in ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json')):
                checksum.update(str(file.relative_to(model_directory)).encode())
                with file.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        checksum.update(block)
        self.identity = checksum.hexdigest()

    def embed(self, texts, query=False):
        with self.lock:
            try:
                method = self.encoder.query_embed if query else self.encoder.passage_embed
                return [unit(vector) for vector in method(texts, batch_size=32)]
            except Exception as error:
                raise SemanticUnavailable('Local embedding inference failed; check model and input encoding') from error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SemanticUnavailable('Embedding redirects are refused to protect authorization headers')


class RemoteEncoder:
    def __init__(self, model, base_url):
        self.model = model
        parsed = urllib.parse.urlparse(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SemanticUnavailable('Embedding URL must not contain credentials, query or fragment')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
            raise SemanticUnavailable('Remote embeddings require HTTPS; HTTP is allowed only on loopback')
        self.url = base_url.rstrip('/') + '/embeddings'
        self.opener = urllib.request.build_opener(NoRedirect())

    def embed(self, texts, query=False):
        output = []
        for start in range(0, len(texts), 32):
            batch = texts[start:start + 32]
            headers = {'Content-Type': 'application/json'}
            key = os.environ.get('MEMORY_EMBEDDING_API_KEY')
            if key:
                headers['Authorization'] = 'Bearer ' + key
            request = urllib.request.Request(self.url, data=json.dumps({'model': self.model, 'input': batch}).encode(), headers=headers)
            try:
                with self.opener.open(request, timeout=20) as response:
                    body = response.read(16 * 1024 * 1024 + 1)
                if len(body) > 16 * 1024 * 1024:
                    raise ValueError('oversized response')
                rows = json.loads(body)['data']
                if sorted(row['index'] for row in rows) != list(range(len(batch))):
                    raise ValueError('missing/duplicate response indices')
                output.extend(unit(row['embedding']) for row in sorted(rows, key=lambda row: row['index']))
            except Exception as error:
                raise SemanticUnavailable('Embedding request failed or returned invalid vectors; no credentials logged') from error
        return output


@functools.lru_cache(maxsize=4)
def _encoder(provider, model, base_url):
    if provider == 'local':
        return LocalEncoder(model)
    if provider == 'openai':
        if not base_url:
            raise SemanticUnavailable('MEMORY_EMBEDDING_BASE_URL is required for explicit remote mode')
        return RemoteEncoder(model, base_url)
    raise SemanticUnavailable('Unknown embedding provider')


def provider():
    name = os.environ.get('MEMORY_EMBEDDING_PROVIDER', 'local')
    model = os.environ.get('MEMORY_EMBEDDING_MODEL', DEFAULT_MODEL if name == 'local' else '')
    base_url = os.environ.get('MEMORY_EMBEDDING_BASE_URL', '') if name == 'openai' else ''
    if not model:
        raise SemanticUnavailable('An embedding model must be configured')
    encoder = _encoder(name, model, base_url)
    fingerprint = hashlib.sha256(json.dumps([name, model, base_url, getattr(encoder, 'identity', ''),
                                           os.environ.get('MEMORY_EMBEDDING_REVISION', '')]).encode()).hexdigest()
    return encoder, fingerprint


def text_for(item):
    return '\n'.join(str(item.get(field) or '') for field in ('text', 'value', 'tags', 'alias', 'fact'))[:8000]


def digest(item):
    return hashlib.sha256(text_for(item).encode('utf-8')).hexdigest()


def _connect(store_path):
    target = pathlib.Path(str(store_path) + '.vectors.sqlite')
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=5)
    connection.execute('CREATE TABLE IF NOT EXISTS vectors (provider TEXT, id TEXT, scope TEXT, digest TEXT, vector TEXT, PRIMARY KEY(provider,id,scope))')
    connection.commit()
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return connection


def rank(store_path, query, rows, mode='hybrid', tokens=None):
    """Compute outside the store lock. Caller must revalidate rows before commit."""
    import memory as M
    lexical_tokens = M.expand_tokens(query) if tokens is None else tokens
    lexical = {item['id']: M.score(item, lexical_tokens) for _, item in rows}
    metadata = {'requested': mode, 'used': 'lexical', 'reason': None}
    similarities, pending, fingerprint = {}, [], None
    if mode != 'lexical' and query and rows:
        try:
            encoder, fingerprint = provider()
            with contextlib.closing(_connect(store_path)) as connection:
                cached = {(row[0], row[1]): (row[2], row[3]) for row in connection.execute(
                    'SELECT id,scope,digest,vector FROM vectors WHERE provider=?', (fingerprint,))}
            vectors, missing = {}, []
            for _, item in rows:
                cache_key = (item['id'], item.get('scope', 'default'))
                entry = cached.get(cache_key)
                if entry and entry[0] == digest(item):
                    vectors[item['id']] = unit(json.loads(entry[1]))
                else:
                    M.protect_text(text_for(item))
                    missing.append(item)
            if missing:
                embedded = encoder.embed([text_for(item) for item in missing])
                if len(embedded) != len(missing):
                    raise SemanticUnavailable('Incomplete embedding batch')
                for item, vector in zip(missing, embedded):
                    vectors[item['id']] = vector
                    pending.append((fingerprint, item['id'], item.get('scope', 'default'), digest(item), json.dumps(vector)))
            query_vector = encoder.embed([M.protect_text(query)], query=True)[0]
            for _, item in rows:
                vector = vectors[item['id']]
                if len(vector) != len(query_vector):
                    raise SemanticUnavailable('Embedding dimension mismatch; rebuild the index')
                similarities[item['id']] = sum(a * b for a, b in zip(vector, query_vector))
            metadata['used'] = mode
        except (SemanticUnavailable, OSError, ValueError, sqlite3.Error) as error:
            if mode == 'semantic':
                raise SemanticUnavailable(str(error)) from error
            metadata['reason'] = str(error)
            pending = []
            similarities = {}
    threshold = float(os.environ.get('MEMORY_SEMANTIC_THRESHOLD', '0.45'))
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('MEMORY_SEMANTIC_THRESHOLD must be between 0 and 1')
    lexical_order = sorted((item['id'] for _, item in rows if lexical[item['id']] > 0), key=lambda identity: -lexical[identity])
    vector_order = sorted((identity for identity, value in similarities.items() if value >= threshold), key=lambda identity: -similarities[identity])
    lexical_rank = {identity: rank for rank, identity in enumerate(lexical_order)}
    vector_rank = {identity: rank for rank, identity in enumerate(vector_order)}
    scores = {}
    for layer, item in rows:
        identity = item['id']
        if not query:
            scores[identity] = M.item_weight(item, layer)
        elif mode == 'semantic' and identity in vector_rank:
            scores[identity] = similarities[identity]
        elif mode != 'semantic' and (identity in lexical_rank or identity in vector_rank):
            scores[identity] = (1 / (60 + lexical_rank[identity]) if identity in lexical_rank else 0) + \
                               (1 / (60 + vector_rank[identity]) if identity in vector_rank else 0)
    ranked = [(layer, item, {'score': scores[item['id']], 'lexical': lexical[item['id']],
                           'semantic': similarities.get(item['id'])}) for layer, item in rows if item['id'] in scores]
    ranked.sort(key=lambda row: (-row[2]['score'], -M.item_weight(row[1], row[0]), row[1]['id']))
    return ranked, pending, metadata


def commit_pending(store_path, pending, active):
    """Called under the authoritative store lock; never resurrect purged vectors."""
    valid = {(item['id'], item.get('scope', 'default')): digest(item) for _, item in active}
    rows = [row for row in pending if valid.get((row[1], row[2])) == row[3]]
    if rows:
        with contextlib.closing(_connect(store_path)) as connection:
            connection.executemany('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)', rows)
            connection.commit()
