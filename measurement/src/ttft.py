"""Ready-model client-observed TTFT; separate from llama-bench TPS."""
import csv
import contextlib
import http.client
import json
import math
import os
from pathlib import Path
import signal
import socket
import statistics
import subprocess
import time

FIELDS = ["request_id", "threads", "prompt_tokens", "repetition", "status",
          "ttft_ms", "first_token_id", "evaluated_tokens", "error"]


def validate_options(req, settings):
    if type(req.get("ttft", True)) is not bool:
        raise ValueError("ttft는 true/false여야 합니다")
    for key in ("ttft_startup_timeout_seconds", "ttft_request_timeout_seconds"):
        value = settings.get(key, 300)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key}는 양수여야 합니다")


def request(port, endpoint, payload=None, timeout=300):
    """No proxy/environment routing: connect only to our loopback server."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        body = None if payload is None else json.dumps(payload).encode()
        conn.request("GET" if body is None else "POST", endpoint, body,
                     {"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}: {response.read(4096)!r}")
        return json.loads(response.read())
    finally:
        conn.close()


def prompt_for_length(tokens, count):
    if not tokens or any(type(t) is not int for t in tokens):
        raise ValueError("tokenize 응답에 정수 토큰이 없습니다")
    # Preserve the tokenizer's leading special token; repeat ordinary seed tokens.
    tail = tokens[1:] or tokens
    return [tokens[0]] + [tail[i % len(tail)] for i in range(count - 1)]


def measure(port, tokens, timeout):
    payload = {"prompt": tokens, "n_predict": 1, "stream": True,
               "cache_prompt": False, "return_tokens": True, "temperature": 0,
               "seed": 1, "ignore_eos": True, "id_slot": 0}
    body = json.dumps(payload).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    first_ms = first_id = None
    final = None
    pending = b""
    started = time.perf_counter()
    try:
        conn.request("POST", "/completion", body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f"completion HTTP {response.status}")
        while True:
            remaining = timeout - (time.perf_counter() - started)
            if remaining <= 0:
                raise TimeoutError("TTFT request deadline exceeded")
            # read1 returns available bytes instead of waiting for a large buffer.
            sock = conn.sock or response.fp.raw._sock
            sock.settimeout(remaining)
            chunk = response.read1(65536)
            observed_ms = (time.perf_counter() - started) * 1000
            if not chunk:
                break
            pending += chunk
            if len(pending) > 1024 * 1024:
                raise ValueError("SSE event too large")
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    continue
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise ValueError("SSE data must be an object")
                if "error" in event:
                    raise ValueError(str(event["error"]))
                generated = event.get("tokens", [])
                if not isinstance(generated, list) or any(type(t) is not int for t in generated):
                    raise ValueError("SSE tokens must be integer IDs")
                if first_ms is None and generated:
                    first_ms, first_id = observed_ms, generated[0]
                if event.get("stop"):
                    final = event
        if first_ms is None or final is None:
            raise ValueError("first token 또는 완료 이벤트가 없습니다")
        timings = final.get("timings")
        evaluated = timings.get("prompt_n") if isinstance(timings, dict) else None
        # tokens_cached describes the slot after completion in b11706; it is
        # not the number of tokens skipped. prompt_n counts evaluated tokens.
        if evaluated != len(tokens) or final.get("truncated", False):
            raise ValueError(f"입력 처리 검증 실패: prompt_n={evaluated}, expected={len(tokens)}")
        return {"ttft_ms": round(first_ms, 6), "first_token_id": first_id,
                "evaluated_tokens": evaluated}
    finally:
        conn.close()


def stop_server(proc):
    if proc.poll() is not None:
        return
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row["threads"], row["prompt_tokens"]), []).append(row)
    result = []
    for (threads, prompt), items in groups.items():
        values = [r["ttft_ms"] for r in items if r["status"] == "ok"]
        mean = statistics.mean(values) if values else None
        sd = statistics.stdev(values) if len(values) > 1 else None
        result.append({"threads": threads, "prompt_tokens": prompt,
                       "ok": len(values), "failed": len(items) - len(values),
                       "mean_ms": mean, "stddev_ms": sd,
                       "cv_percent": sd / mean * 100 if sd is not None and mean else None})
    return result


def run_ttft(req, settings, model, out_dir, request_id):
    validate_options(req, settings)
    server = Path(settings.get("server_path", str(Path(settings["bench_path"]).with_name("llama-server"))))
    rows = []
    info = {"enabled": req.get("ttft", True), "server_path": str(server),
            "definition": "client-observed monotonic wall time immediately before POST to first SSE generated token ID; includes loopback HTTP, tokenization excluded using token IDs; model startup and one warmup per condition excluded",
            "cache_prompt": False, "prompt": "synthetic token IDs from tokenize(Hello world., add_special=true); repeated to exact length; not a chat prompt",
            "repetitions": "repeat measured requests per threads/prompt_tokens condition; one unrecorded warmup per condition; gen_tokens does not multiply TTFT conditions",
            "status": "disabled" if not req.get("ttft", True) else "pending", "conditions": []}
    def values(key):
        v = req[key]
        return v if isinstance(v, list) else [v]
    with (out_dir / "ttft.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        if not info["enabled"]:
            return info
        for threads in dict.fromkeys(values("threads")):
            proc = None
            error = None
            try:
                if not server.is_file():
                    raise FileNotFoundError(f"llama-server가 없습니다: {server}")
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    port = reservation.getsockname()[1]
                command = [str(server), "-m", str(model), "--host", "127.0.0.1",
                           "--port", str(port), "-t", str(threads), "-tb", str(threads),
                           "-ngl", "0", "-np", "1", "-c", str(max(values("prompt_tokens")) + 32)]
                with (out_dir / f"ttft-server-t{threads}.log").open("w", encoding="utf-8") as log:
                    log.write(json.dumps(command) + "\n"); log.flush()
                    proc = subprocess.Popen(command, stdout=log, stderr=log,
                                            start_new_session=os.name == "posix")
                    deadline = time.monotonic() + settings.get("ttft_startup_timeout_seconds", 300)
                    while True:
                        if proc.poll() is not None:
                            raise RuntimeError(f"server exited: {proc.returncode}")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("TTFT server startup timeout")
                        try:
                            request(port, "/health", timeout=min(1, remaining))
                            break
                        except (OSError, ValueError, http.client.HTTPException):
                            time.sleep(min(0.1, remaining))
                    timeout = settings.get("ttft_request_timeout_seconds", 300)
                    tokenized = request(port, "/tokenize", {"content": "Hello world.", "add_special": True}, timeout=timeout)
                    if not isinstance(tokenized, dict) or "tokens" not in tokenized:
                        raise ValueError("tokenize response has no tokens")
                    seed = tokenized["tokens"]
                    for prompt in dict.fromkeys(values("prompt_tokens")):
                        tokens = prompt_for_length(seed, prompt)
                        warmup_error = None
                        try:
                            measure(port, tokens, timeout)
                        except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
                            warmup_error = f"warmup failed: {exc}"
                        for repetition in range(1, req["repeat"] + 1):
                            row = {"request_id": request_id, "threads": threads,
                                   "prompt_tokens": prompt, "repetition": repetition}
                            try:
                                if warmup_error:
                                    raise RuntimeError(warmup_error)
                                row.update(measure(port, tokens, timeout), status="ok")
                            except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
                                row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                            rows.append(row); writer.writerow(row); output.flush()
                            if row["status"] != "ok":
                                # The server may still be processing a timed-out request.
                                # Abort this server rather than queueing another measurement.
                                raise RuntimeError(row["error"])
            except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                if proc is not None:
                    stop_server(proc)
            if error:
                for prompt in dict.fromkeys(values("prompt_tokens")):
                    for repetition in range(1, req["repeat"] + 1):
                        if any(r["threads"] == threads and r["prompt_tokens"] == prompt and r["repetition"] == repetition for r in rows):
                            continue
                        row = {"request_id": request_id, "threads": threads, "prompt_tokens": prompt,
                               "repetition": repetition, "status": "unavailable", "error": error}
                        rows.append(row); writer.writerow(row); output.flush()
    info["conditions"] = summarize(rows)
    ok = sum(r["status"] == "ok" for r in rows)
    info.update(ok=ok, failed=len(rows) - ok, status="ok" if ok == len(rows) else "partial" if ok else "unavailable")
    return info
