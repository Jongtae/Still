"""Offline, disposable #849 compatibility measurements; no Still runtime imports.

Run with --model-dir DIR --out FILE.json. --quick reduces corpus/search sizes.
All optional dependencies are imported inside functions. Errors are evidence.
"""
from __future__ import annotations

import argparse
import ast
import gc
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import resource
import socket
import sqlite3
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

PAIRS = [
    ("내일 병원 예약이 몇 시인지 알려 줘", "내일 오후 세 시에 치과 진료를 예약했다."),
    ("지난 회의의 결정 사항을 찾아 줘", "지난 팀 회의에서 출시를 다음 달로 미루기로 했다."),
    ("주말에 어디서 자전거를 타기로 했지", "토요일 아침 한강 공원에서 자전거를 타기로 했다."),
    ("내가 피해야 하는 음식은 무엇인가", "나는 땅콩 알레르기가 있어서 땅콩이 든 음식을 피한다."),
    ("Where did I put my spare keys?", "The spare house keys are in the blue kitchen drawer."),
    ("When is the project deadline?", "The final project report is due next Friday afternoon."),
    ("What kind of coffee do I prefer?", "I usually order decaffeinated coffee with oat milk."),
    ("Where is my passport stored?", "여권은 침실 서랍 안의 초록색 파일에 보관하고 있다."),
    ("다음 주 기차는 언제 출발하지", "My train to Busan departs at nine on Monday morning."),
    ("What should I bring to the hiking trip?", "등산 갈 때는 물 두 병과 방수 재킷을 챙기기로 했다."),
]
VARIANTS = ("model.onnx", "model_O4.onnx", "model_qint8_avx512_vnni.onnx")
ENV_FLAGS = ("HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_IMPLICIT_TOKEN", "ORT_DISABLE_TELEMETRY")


def error(exc):
    return {"error_type": type(exc).__name__, "error": str(exc)}


def capture(fn):
    try:
        return fn()
    except Exception as exc:
        return error(exc)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


@contextmanager
def offline():
    # Set before importing any spike package, including in each fresh worker.
    for key in ENV_FLAGS:
        os.environ[key] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    attempts = []
    originals = (socket.socket.connect, socket.create_connection, socket.getaddrinfo)

    def block(name):
        def blocked(*args, **kwargs):
            # Synthetic fixture only; never run this harness on private endpoints.
            attempts.append({"call": name, "args": repr(args[1:] if name == "connect" else args)})
            raise RuntimeError("offline socket guard: " + name)
        return blocked

    socket.socket.connect = block("connect")
    socket.create_connection = block("create_connection")
    socket.getaddrinfo = block("getaddrinfo")
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.create_connection, socket.getaddrinfo = originals


def load_vec(conn):
    import sqlite_vec
    path = sqlite_vec.loadable_path()
    try:
        conn.enable_load_extension(True)
        conn.load_extension(path)
    finally:
        conn.enable_load_extension(False)
    return path


def sqlite_probe(folder):
    result = {"enable_load_extension_present": hasattr(sqlite3.Connection, "enable_load_extension")}
    with sqlite3.connect(":memory:") as conn:
        if result["enable_load_extension_present"]:
            try:
                path = load_vec(conn)
                binary = next(p for p in (Path(path), Path(path + ".dylib"), Path(path + ".so"), Path(path + ".dll")) if p.is_file())
                result.update(loadable_path=path, binary=str(binary), binary_sha256=digest(binary),
                              vec_version=conn.execute("select vec_version()").fetchone()[0])
                try:
                    conn.load_extension(path)
                    result["reload_after_disable"] = {"raised": False}
                except Exception as exc:
                    result["reload_after_disable"] = {"raised": True, **error(exc)}
            except Exception as exc:
                result["load"] = error(exc)
        else:
            result["load"] = {"unavailable": "CPython SQLite extension-loading API absent"}
    layouts = {}
    for layout in ("blob", "vec0"):
        def probe():
            db = Path(folder) / (layout + ".sqlite")
            conn = sqlite3.connect(db)
            try:
                conn.executescript("create table memories(id integer primary key, body text); create table messages(id integer primary key, body text);")
                if layout == "vec0":
                    load_vec(conn)
                    conn.execute("create virtual table vectors using vec0(embedding float[384] distance_metric=cosine)")
                else:
                    conn.execute("create table vectors(rowid integer primary key, embedding blob)")
                conn.execute("insert into vectors(rowid,embedding) values(1,?)", (b'\0' * 1536,))
                conn.commit()
            finally:
                conn.close()
            plain = sqlite3.connect(db)
            observed = {}
            try:
                # No coupling triggers: distinguishes canonical availability from vector access.
                for table in ("memories", "messages"):
                    for action, sql in (("insert", f"insert into {table} values(1,'fixture')"),
                                        ("select", f"select * from {table}"),
                                        ("delete", f"delete from {table} where id=1")):
                        observed[table + "." + action] = capture(lambda sql=sql: plain.execute(sql).fetchall())
                plain.commit()
                for action, sql, params in (
                    ("select", "select rowid from vectors", ()),
                    ("insert", "insert into vectors(rowid,embedding) values(2,?)", (b'\0' * 1536,)),
                    ("delete", "delete from vectors where rowid=1", ()),
                ):
                    observed["vectors." + action] = capture(lambda sql=sql, params=params: plain.execute(sql, params).fetchall())
                plain.commit()
                observed["vacuum"] = capture(lambda: plain.execute("vacuum").fetchall())
                if layout == "vec0":
                    plain.execute("create trigger purge_vector after delete on memories begin delete from vectors where rowid=old.id; end")
                    plain.execute("insert into memories values(2,'fixture')")
                    observed["canonical_delete_with_vector_trigger"] = capture(lambda: plain.execute("delete from memories where id=2").fetchall())
            finally:
                plain.close()
            return observed
        layouts[layout] = capture(probe)
    result["plain_connection"] = layouts
    return result


class Thin:
    def __init__(self, model_dir, variant="model.onnx"):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 2
        self.session = ort.InferenceSession(str(Path(model_dir) / "onnx" / variant), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("<pad>"), pad_token="<pad>")

    def embed(self, texts):
        import numpy as np
        outputs = []
        names = {x.name for x in self.session.get_inputs()}
        for start in range(0, len(texts), 16):
            encoded = self.tokenizer.encode_batch(texts[start:start + 16])
            feed = {"input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                    "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64)}
            hidden = self.session.run(None, {k: v for k, v in feed.items() if k in names})[0]
            mask = feed["attention_mask"][..., None]
            pooled = (hidden.astype(np.float32) * mask).sum(1) / np.maximum(mask.sum(1), 1)
            pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            outputs.append(pooled.astype(np.float32))
        return np.concatenate(outputs)


class Fast:
    def __init__(self, model_dir, cache_dir):
        import onnxruntime as ort
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType
        ort.disable_telemetry_events()
        name = "spike/multilingual-e5-small-local"
        if not any(x["model"] == name for x in TextEmbedding.list_supported_models()):
            TextEmbedding.add_custom_model(model=name, pooling=PoolingType.MEAN, normalization=True,
                                          sources=ModelSource(hf="intfloat/multilingual-e5-small"), dim=384, model_file="onnx/model.onnx")
        self.model = TextEmbedding(model_name=name, specific_model_path=str(model_dir),
                                   cache_dir=str(cache_dir), local_files_only=True, threads=2,
                                   providers=["CPUExecutionProvider"])

    def embed(self, texts):
        import numpy as np
        # Prefixes are explicit; CustomTextEmbedding does not supply E5 prefixes.
        return np.stack(list(self.model.embed(texts, batch_size=16, parallel=None)))


def memory_corpus(n):
    bases = [p for pair in PAIRS for p in pair]
    result = []
    for i in range(n):
        base = bases[i % len(bases)] + " "
        target = 30 + (i * 37 % 171)
        result.append((base * (target // len(base) + 1))[:target])
    return result


def worker(path, model_dir, variant, quick):
    result = {"path": path, "variant": variant, "threads": 2, "batch_size": 16,
              "cold_definition": "fresh process; imports + constructor; OS file cache not flushed"}
    with offline() as attempts, tempfile.TemporaryDirectory(prefix="embedding-compat-") as cache:
        def checkpoint(stage):
            result["in_progress"] = stage
            result["attempted_connections"] = list(attempts)
            result["peak_rss_bytes"] = rss()
            result["offline_env"] = {k: os.environ[k] for k in ENV_FLAGS}
            print(json.dumps(result), flush=True)

        try:
            factory = lambda: Thin(model_dir, variant) if path == "thin" else Fast(model_dir, cache)
            checkpoint("cold_load")
            start = time.perf_counter()
            model = factory()
            result["cold_load_s"] = time.perf_counter() - start
            result["rss_after_cold_load_bytes"] = rss()
            start = time.perf_counter()
            model.embed(["query: " + PAIRS[0][0]])
            result["first_query_s"] = time.perf_counter() - start
            del model
            gc.collect()
            start = time.perf_counter()
            model = factory()
            result["warm_reload_s"] = time.perf_counter() - start
            query_times = []
            for _ in range(5):
                start = time.perf_counter()
                model.embed(["query: " + PAIRS[0][0]])
                query_times.append(time.perf_counter() - start)
            result["query_s"] = {"samples": query_times, "median": statistics.median(query_times)}
            texts = ["query: " + q for q, _ in PAIRS] + ["passage: " + p for _, p in PAIRS]
            vectors = model.embed(texts)
            result["fixture_vectors"] = vectors.tolist()
            scores = vectors[:10] @ vectors[10:].T
            result["search_scores"] = scores.tolist()
            result["search_ranks"] = (-scores).argsort(axis=1).tolist()
            result["paired_top1_count"] = sum(int(scores[i].argmax()) == i for i in range(10))
            tokenizer = model.tokenizer if path == "thin" else model.model.model.tokenizer
            result["truncation"] = tokenizer.truncation
            result["long_input_tokens"] = len(tokenizer.encode("query: " + "긴 문장 long sentence " * 1000).ids)
            result["actual_providers"] = (model.session if path == "thin" else model.model.model.model).get_providers()
            result["batches"] = {}
            checkpoint("batch_costs")
            # fp32 paths bear the batch-cost comparison; variants are compatibility probes.
            if variant == "model.onnx":
                for n in ([10, 100] if quick else [100, 1000]):
                    corpus = memory_corpus(n)
                    checkpoint("batch_" + str(n))
                    start = time.perf_counter()
                    embedded = model.embed(["passage: " + text for text in corpus])
                    result["batches"][str(n)] = {"seconds": time.perf_counter() - start,
                        "shape": list(embedded.shape), "min_chars": min(map(len, corpus)),
                        "max_chars": max(map(len, corpus)), "peak_rss_bytes": rss()}
            result["in_progress"] = None
            result["cache_files"] = [str(p.relative_to(cache)) for p in Path(cache).rglob("*") if p.is_file()]
        except Exception as exc:
            result.update(error(exc))
        result["attempted_connections"] = attempts
        result["peak_rss_bytes"] = rss()
        result["offline_env"] = {k: os.environ[k] for k in ENV_FLAGS}
    return result


def child(path, model_dir, variant, quick):
    cmd = [sys.executable, "-B", "-m", "evals.embedding_spike.compat", "--worker", path,
           "--model-dir", str(model_dir), "--variant", variant]
    if quick:
        cmd.append("--quick")
    start = time.perf_counter()
    try:
        run = subprocess.run(cmd, text=True, capture_output=True, timeout=210)
        result = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {}
        result.update(process_wall_s=time.perf_counter() - start, returncode=run.returncode,
                      stderr=run.stderr[-12000:])
        return result
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        if isinstance(raw, bytes):
            raw = raw.decode(errors="replace")
        lines = raw.strip().splitlines()
        result = json.loads(lines[-1]) if lines else {}
        result["worker_timeout"] = error(exc)
        result["process_wall_s"] = time.perf_counter() - start
        result["returncode"] = None
        result["stderr"] = (exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        stage = result.get("in_progress", "unknown")
        if stage.startswith("batch_") and stage[6:].isdigit():
            result.setdefault("batches", {})[stage[6:]] = error(exc)
        return result
    except Exception as exc:
        return error(exc)


def agreement(base, other):
    import numpy as np
    a = np.array(base["fixture_vectors"])
    b = np.array(other["fixture_vectors"])
    cos = (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    rank_a = np.array(base["search_ranks"])
    rank_b = np.array(other["search_ranks"])
    rho = 1 - 6 * ((rank_a.argsort(1) - rank_b.argsort(1)) ** 2).sum(1) / (10 * (10 ** 2 - 1))
    return {"cosines": cos.tolist(), "min_cosine": float(cos.min()), "mean_cosine": float(cos.mean()),
            "top1_agreement": float((rank_a[:, 0] == rank_b[:, 0]).mean()),
            "mean_spearman_rho": float(rho.mean()), "fixture_pairs": PAIRS}


def search_cost(folder, quick):
    import numpy as np
    results = {}
    rng = np.random.default_rng(849)
    for n in ([1000] if quick else [1000, 10000, 50000]):
        conn = sqlite3.connect(Path(folder) / f"search-{n}.sqlite")
        try:
            extension = capture(lambda: load_vec(conn))
            vectors = rng.normal(size=(n, 384)).astype(np.float32)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            query = rng.normal(size=384).astype(np.float32)
            query /= np.linalg.norm(query)
            conn.execute("create table blobs(id integer primary key, embedding blob)")
            conn.executemany("insert into blobs values(?,?)", ((i, v.tobytes()) for i, v in enumerate(vectors)))
            conn.commit()
            current = {"vector_bytes": n * 384 * 4, "extension": extension, "methods": {}}
            vec_ready = False
            if isinstance(extension, str):
                def setup():
                    conn.execute("create virtual table knn using vec0(embedding float[384] distance_metric=cosine)")
                    conn.executemany("insert into knn(rowid,embedding) values(?,?)", ((i, v.tobytes()) for i, v in enumerate(vectors)))
                    conn.commit()
                    return True
                current["vec0_setup"] = capture(setup)
                vec_ready = current["vec0_setup"] is True
            read_times = []
            def numpy_scan():
                start = time.perf_counter()
                rows = conn.execute("select id,embedding from blobs order by id").fetchall()
                matrix = np.frombuffer(b"".join(row[1] for row in rows), dtype=np.float32).reshape(n, 384)
                read_times.append(time.perf_counter() - start)
                scores = matrix @ query
                top = np.argpartition(-scores, 19)[:20]
                top = top[np.argsort(-scores[top])]
                return [rows[int(i)][0] for i in top]
            methods = {"numpy_blob": numpy_scan}
            if isinstance(extension, str):
                methods["vec_distance_cosine_blob"] = lambda: [r[0] for r in conn.execute(
                    "select id from blobs order by vec_distance_cosine(embedding,?) limit 20", (query.tobytes(),))]
            else:
                current["methods"]["vec_distance_cosine_blob"] = {"unavailable": extension}
            if vec_ready:
                methods["vec0_knn"] = lambda: [r[0] for r in conn.execute(
                    "select rowid from knn where embedding match ? and k=20 order by distance", (query.tobytes(),))]
            else:
                current["methods"]["vec0_knn"] = {"unavailable": extension}
            for name, fn in methods.items():
                def measure():
                    times = []
                    for _ in range(5):
                        start = time.perf_counter()
                        ids = fn()
                        times.append(time.perf_counter() - start)
                    return {"median_s": statistics.median(times), "samples_s": times, "top20": ids}
                current["methods"][name] = capture(measure)
            current["numpy_read_and_materialize_median_s"] = statistics.median(read_times)
            baseline = current["methods"]["numpy_blob"].get("top20")
            current["exact_top20_agreement"] = {k: v["top20"] == baseline for k, v in current["methods"].items() if "top20" in v}
            current["database_bytes"] = (Path(folder) / f"search-{n}.sqlite").stat().st_size
            results[str(n)] = current
        finally:
            conn.close()
    return results


def hybrid(fts_ids, vec_ids, k=60):
    """Tiny RRF prototype; absent vectors preserve FTS ordering exactly."""
    if not vec_ids:
        return list(fts_ids), "fts5"
    scores = {}
    for ids in (fts_ids, vec_ids):
        for rank, item in enumerate(dict.fromkeys(ids), 1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda item: -scores[item]), "fts5+vector"


def fallback_probe():
    import builtins
    from unittest.mock import patch
    with sqlite3.connect(":memory:") as conn, tempfile.TemporaryDirectory() as folder:
        conn.execute("create virtual table docs using fts5(body)")
        conn.executemany("insert into docs values(?)", [("memory meeting notes",), ("meeting tomorrow",), ("memory recipe",)])
        fts = [r[0] for r in conn.execute("select rowid from docs where docs match 'meeting' order by bm25(docs)")]
        results = {"fts_ids": fts, "available": hybrid(fts, list(reversed(fts)) + [3])}
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == "onnxruntime":
                raise ModuleNotFoundError("simulated missing onnxruntime")
            return original(name, *args, **kwargs)
        for scenario in ("missing_import", "missing_model_dir"):
            try:
                if scenario == "missing_import":
                    with patch("builtins.__import__", missing):
                        Thin(Path(folder) / "absent")
                else:
                    Thin(Path(folder) / "absent")
                results[scenario] = {"unexpected_success": True}
            except Exception as exc:
                ids, mode = hybrid(fts, None)
                results[scenario] = {**error(exc), "ids": ids, "mode": mode, "unchanged": ids == fts}
        return results


def disk_and_versions(model_dir):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    def closure(roots):
        found = set()
        todo = list(roots)
        while todo:
            name = canonicalize_name(todo.pop())
            if name in found:
                continue
            dist = metadata.distribution(name)
            found.add(name)
            for raw in dist.requires or []:
                req = Requirement(raw)
                if req.marker is None or req.marker.evaluate({"extra": ""}):
                    todo.append(req.name)
        return sorted(found)
    thin = closure(["onnxruntime", "tokenizers", "numpy", "sqlite-vec"])
    full = closure(["fastembed", "onnxruntime", "tokenizers", "numpy", "sqlite-vec"])
    distributions = {}
    for name in sorted(set(thin + full)):
        dist = metadata.distribution(name)
        files = [Path(dist.locate_file(f)) for f in dist.files or [] if ".." not in Path(f).parts]
        sizes = {}
        for path in files:
            if path.is_file():
                rel = path.relative_to(dist.locate_file(""))
                sizes[rel.parts[0]] = sizes.get(rel.parts[0], 0) + path.stat().st_size
        distributions[name] = {"version": dist.version, "bytes": sum(sizes.values()), "site_packages_directories": sizes,
                               "license": dist.metadata.get("License-Expression") or dist.metadata.get("License")}
    models = {}
    for path in sorted(Path(model_dir).rglob("*")):
        if path.is_file():
            models[str(path.relative_to(model_dir))] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return {"distributions": distributions, "sets": {key: {"packages": names, "bytes": sum(distributions[n]["bytes"] for n in names)}
            for key, names in (("thin_installed_closure", thin), ("full_installed_closure", full))},
            "measurement": "sum of installed RECORD files inside site-packages, logical bytes, includes metadata; not wheel download sizes or an independently installed thin lock",
            "model_files": models}


def source_scan():
    """Static grep with AST enclosing function attribution; not a call-graph proof."""
    pattern = re.compile(r"telemetry|requests\.(get|post|head|request)|snapshot_download|hf_hub_download|httpx\.|urlopen|from_pretrained|def download_model|specific_model_path|InferenceSession\(")
    results = {}
    for package in ("fastembed", "huggingface-hub", "tokenizers", "onnxruntime"):
        hits = []
        dist = metadata.distribution(package)
        for rel in dist.files or []:
            if str(rel).endswith(".py"):
                path = Path(dist.locate_file(rel))
                source = path.read_text(errors="replace")
                try:
                    tree = ast.parse(source)
                    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                except SyntaxError:
                    scopes = []
                for lineno, line in enumerate(source.splitlines(), 1):
                    if pattern.search(line):
                        enclosing = [n for n in scopes if n.lineno <= lineno <= n.end_lineno]
                        enclosing.sort(key=lambda n: n.lineno)
                        hits.append({"file": str(rel), "line": lineno, "function": ".".join(n.name for n in enclosing) or "<module>", "text": line.strip()[:250]})
        results[package] = {"version": dist.version, "hits": hits}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--worker", choices=("thin", "fastembed"), help=argparse.SUPPRESS)
    parser.add_argument("--variant", default="model.onnx", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.model_dir, args.variant, args.quick)))
        return
    if args.out is None:
        parser.error("--out is required")
    started = time.perf_counter()
    result = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "evidence_class": "local operating observation", "quick": args.quick,
              "harness_sha256": digest(__file__), "model_dir": str(args.model_dir),
              "environment": {"python": sys.version, "python_build": platform.python_build(),
                  "executable": sys.executable, "platform": platform.platform(), "machine": platform.machine(),
                  "sqlite_version": sqlite3.sqlite_version, "config_args": sysconfig.get_config_var("CONFIG_ARGS"),
                  "enable_load_extension_present": hasattr(sqlite3.Connection, "enable_load_extension")}}
    with offline() as attempts, tempfile.TemporaryDirectory(prefix="compat-sqlite-") as folder:
        for name, fn in (("sqlite_vec", lambda: sqlite_probe(folder)),
                         ("disk", lambda: disk_and_versions(args.model_dir)), ("source_scan", source_scan),
                         ("fallback", fallback_probe), ("search", lambda: search_cost(folder, args.quick))):
            print("measuring " + name, file=sys.stderr, flush=True)
            result[name] = capture(fn)
        result["embedding"] = {}
        for name, path, variant in (("thin_fp32", "thin", VARIANTS[0]), ("fastembed_fp32", "fastembed", VARIANTS[0]),
                                    ("thin_O4", "thin", VARIANTS[1]), ("thin_qint8", "thin", VARIANTS[2])):
            print("measuring " + name, file=sys.stderr, flush=True)
            result["embedding"][name] = child(path, args.model_dir, variant, args.quick)
        baseline = result["embedding"]["thin_fp32"]
        result["agreement"] = {key: capture(lambda value=value: agreement(baseline, value))
                               for key, value in result["embedding"].items() if key != "thin_fp32"}
        # Retain scores/ranks/cosines, not redundant 384-dimensional fixture dumps.
        for value in result["embedding"].values():
            value.pop("fixture_vectors", None)
        result["offline"] = {"parent_attempted_connections": attempts,
            "worker_attempt_count": sum(len(v.get("attempted_connections", [])) for v in result["embedding"].values()),
            "flags": {k: os.environ[k] for k in ENV_FLAGS},
            "scope": "Python socket.connect/create_connection/getaddrinfo guard; ORT telemetry disabled via API. No OS-level packet capture or native socket interception."}
    result["elapsed_s"] = time.perf_counter() - started
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(str(args.out), flush=True)


if __name__ == "__main__":
    main()
