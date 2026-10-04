#!/usr/bin/env python3
"""Discover locally served models and write catalog entries from real facts.

An OpenAI-compatible llama.cpp / llama-swap server reports, per model, the
command line its server was launched with. That is ground truth for the things
a hand-written table gets wrong: the context window, the quantization, whether
a vision projector is loaded. This reads it and emits models.json entries, so
the catalog states facts instead of repeating claims.

    python discover.py --print                 # what the server exposes
    python discover.py --out models.json       # write/merge a catalog

Connection details come from the environment or a harness auth file, never from
this repository:

    LLAMA_BASE_URL / LLAMA_API_KEY
    --auth-file <path>   a pi-style auth.json: {"llama.cpp": {"key": ..., "env": {...}}}
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_AUTH = Path(os.environ.get("PI_AUTH_FILE", "~/.pi/agent/auth.json")).expanduser()
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# Starting capability values, keyed only on quantization - the one quality
# signal the server actually reports. These are a starting point for the
# operator to tune, not a measurement, and the catalog records that.
QUANT_BASE = {"Q8": 0.55, "Q6": 0.52, "Q5": 0.49, "Q4": 0.46, "IQ4": 0.44, "IQ3": 0.38,
              "Q3": 0.38, "BF16": 0.60, "F16": 0.60}
CODER_HINTS = ("coder", "code", "deepseek-coder", "qwen2.5-coder")


def _safe_url(url: str) -> str:
    """Return a diagnostic URL without credentials or request-only components."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<configured server>"
    if not parts.scheme or not parts.netloc:
        return "<configured server>"
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def creds(auth_file: Path | None) -> tuple[str, str | None]:
    base, key = os.environ.get("LLAMA_BASE_URL"), os.environ.get("LLAMA_API_KEY")
    if base and key:
        return base.rstrip("/"), key
    p = auth_file or DEFAULT_AUTH
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            sys.exit(f"could not read {p}: {exc}")
        for provider, cfg in data.items():
            if not isinstance(cfg, dict):
                continue
            env = cfg.get("env") or {}
            found = base or env.get("LLAMA_BASE_URL") or env.get("OPENAI_BASE_URL")
            if found:
                return found.rstrip("/"), key or cfg.get("key")
    sys.exit("no server URL. Set LLAMA_BASE_URL (and LLAMA_API_KEY), or pass --auth-file.")


def fetch_models(base: str, key: str | None, timeout: float = 20.0) -> list[dict]:
    url = f"{base}/v1/models"
    display_url = _safe_url(url)
    try:
        req = urllib.request.Request(url)
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                sys.exit(
                    f"response from {display_url} exceeds the "
                    f"{MAX_RESPONSE_BYTES // (1024 * 1024)} MiB limit"
                )
            payload = json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        sys.exit(
            f"{display_url} returned HTTP {exc.code}. "
            "Check LLAMA_API_KEY / the auth file."
        )
    except (UnicodeError, json.JSONDecodeError):
        sys.exit(f"invalid response from {display_url}: malformed JSON")
    except (urllib.error.URLError, OSError, ValueError):
        # Exception text from URL libraries can repeat the original URL, including
        # userinfo. The sanitized endpoint is enough context for a useful error.
        sys.exit(f"could not reach {display_url}")

    if not isinstance(payload, dict):
        sys.exit(f"invalid response from {display_url}: expected a JSON object")
    models = payload.get("data")
    if not isinstance(models, list):
        sys.exit(f'invalid response from {display_url}: expected "data" to be a list')

    valid = []
    for i, entry in enumerate(models):
        if not isinstance(entry, dict):
            print(f"warning: models[{i}] is not an object; skipped", file=sys.stderr)
            continue
        if not isinstance(entry.get("id"), str) or not entry["id"].strip():
            print(f"warning: models[{i}] has no string id; skipped", file=sys.stderr)
            continue
        valid.append(entry)
    return valid


def _arg(args: list[str], *names: str) -> str | None:
    for n in names:
        if n in args:
            i = args.index(n)
            if i + 1 < len(args):
                return args[i + 1]
    return None


def facts(entry: dict) -> dict:
    """What the server's own launch command says about one model."""
    status = entry.get("status") or {}
    if not isinstance(status, dict):
        status = {}
    args = status.get("args") or []
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        args = []
    aliases = entry.get("aliases") or []
    alias = aliases[0] if isinstance(aliases, list) and aliases \
        and isinstance(aliases[0], str) else None
    identifier = entry.get("id") if isinstance(entry.get("id"), str) else None
    model_path = _arg(args, "-m", "--model") or ""
    file = model_path.rsplit("/", 1)[-1]
    quant = re.search(r"(IQ\d_\w+|Q\d_\w_\w+|Q\d_\w+|Q\d|BF16|F16)", file, re.I)
    ctx = _arg(args, "-c", "--ctx-size")
    params = re.search(r"(\d+(?:\.\d+)?)B", file)
    moe = re.search(r"A(\d+(?:\.\d+)?)B", file)
    return {
        "id": identifier,
        "alias": alias,
        "context_window": int(ctx) if ctx and ctx.isdigit() else None,
        "quant": quant.group(1) if quant else None,
        "vision": bool(_arg(args, "--mmproj")),
        "speculative": "--mtp" in args or bool(re.search(r"mtp", file, re.I)),
        "params_b": float(params.group(1)) if params else None,
        "active_params_b": float(moe.group(1)) if moe else None,
        "loaded": status.get("value"),
        "file": file or None,
        "coder_specialized": any(h in (identifier or "").lower()
                                 or h in file.lower() for h in CODER_HINTS),
    }


def _write_atomic(path: Path, text: str) -> None:
    """Replace a catalog atomically so interruption cannot leave partial JSON."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as fh:
            temporary = Path(fh.name)
            os.fchmod(fh.fileno(), 0o600)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _load_existing_catalog(path: Path) -> dict:
    """Load a bounded catalog and retain only entries safe for merging."""
    try:
        with path.open("rb") as fh:
            body = fh.read(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        sys.exit(f"could not read existing catalog {path}: {exc}")
    if len(body) > MAX_RESPONSE_BYTES:
        sys.exit(
            f"existing catalog {path} exceeds the "
            f"{MAX_RESPONSE_BYTES // (1024 * 1024)} MiB limit"
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        sys.exit(f"could not read existing catalog {path}: {exc}")
    if not isinstance(payload, dict):
        sys.exit(f"invalid existing catalog {path}: expected a JSON object")
    models = payload.get("models")
    if not isinstance(models, list):
        sys.exit(f'invalid existing catalog {path}: expected "models" to be a list')

    valid = []
    for i, entry in enumerate(models):
        if not isinstance(entry, dict):
            print(f"warning: existing models[{i}] is not an object; skipped",
                  file=sys.stderr)
            continue
        if not isinstance(entry.get("id"), str) or not entry["id"].strip():
            print(f"warning: existing models[{i}] has no string id; skipped",
                  file=sys.stderr)
            continue
        if entry.get("role") is not None and not isinstance(entry["role"], str):
            print(f"warning: existing models[{i}] has a non-string role; skipped",
                  file=sys.stderr)
            continue
        valid.append(entry)
    payload["models"] = valid
    if not isinstance(payload.get("provenance", ""), str):
        print("warning: existing provenance is not a string; ignored", file=sys.stderr)
        payload["provenance"] = ""
    return payload


def capabilities(f: dict) -> dict[str, float]:
    fam = (f.get("quant") or "Q4").upper()
    base = next((v for k, v in QUANT_BASE.items() if fam.startswith(k)), 0.46)
    coder = 0.08 if f["coder_specialized"] else 0.0
    def c(x: float) -> float:
        return round(max(0.0, min(1.0, x)), 2)
    return {
        # A quantized local model is explicitly not trusted with architecture.
        "planning": c(base - 0.20),
        "coding": c(base + coder),
        "debugging": c(base - 0.05 + coder / 2),
        "creativity": c(base - 0.10),
        "research": c(base),
    }


def to_entry(f: dict, role: str) -> dict:
    return {
        "id": f["id"],
        "role": role,
        "harness": "pi",
        "launchArgs": ["--model", f["id"]],
        "contextWindow": f["context_window"],
        "capabilities": capabilities(f),
        "unmetered": True,
        "quantized": True,
        "images": f["vision"],
        "notes": " ".join(x for x in [
            f["alias"], f"quant {f['quant']}" if f["quant"] else None,
            "vision" if f["vision"] else None,
            "speculative decoding" if f["speculative"] else None,
        ] if x),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--auth-file", type=Path, help="pi-style auth.json holding the key")
    p.add_argument("--print", action="store_true", dest="show",
                   help="print what the server exposes and exit")
    p.add_argument("--out", type=Path, help="write/merge a catalog here")
    p.add_argument("--role", default="LOCAL_WORKER",
                   help="role to assign discovered models (default: LOCAL_WORKER)")
    p.add_argument("--only", nargs="+", help="only these model ids")
    args = p.parse_args()

    base, key = creds(args.auth_file)
    found = [facts(m) for m in fetch_models(base, key)]
    found = [f for f in found if f["id"] and (not args.only or f["id"] in args.only)]
    found.sort(key=lambda f: -(f["context_window"] or 0))

    if args.show or not args.out:
        print(f"{len(found)} models exposed by the configured server\n")
        print(f"  {'id':24} {'context':>9}  {'quant':8} {'params':>7} vis spec  state")
        for f in found:
            ctx = f"{f['context_window']:,}" if f["context_window"] else "?"
            par = (f"{f['params_b']:g}B" + (f"/A{f['active_params_b']:g}B"
                   if f["active_params_b"] else "")) if f["params_b"] else "?"
            print(f"  {f['id']:24} {ctx:>9}  {(f['quant'] or '?'):8} {par:>7} "
                  f"{'yes' if f['vision'] else ' - '} {'yes' if f['speculative'] else ' - '}  {f['loaded']}")
        for f in found:
            if f["alias"]:
                print(f"    {f['id']:24} {f['alias']}")
        if not args.out:
            return 0

    entries = {f["id"]: to_entry(f, args.role) for f in found}
    existing: dict = {}
    if args.out.exists():
        existing = _load_existing_catalog(args.out)
        kept = {m["id"]: m for m in existing.get("models", [])}
        for ident, entry in entries.items():
            if ident in kept:
                # Never clobber an operator's tuned numbers; refresh only facts.
                for fact_key in ("contextWindow", "images", "harness", "notes"):
                    if entry.get(fact_key) is not None:
                        kept[ident][fact_key] = entry[fact_key]
            else:
                kept[ident] = entry
        models = list(kept.values())
    else:
        models = list(entries.values())

    doc = {
        "updatedAt": datetime.date.today().isoformat(),
        "provenance": existing.get("provenance") or (
            "Local models and their facts (context window, quantization, vision) are "
            "discovered from the inference server's own launch arguments by "
            "discover.py. Capability numbers are starting values derived from "
            "quantization alone - tune them. Hosted models are operator-curated."),
        "models": sorted(models, key=lambda m: (m.get("role") or "", m["id"])),
    }
    _write_atomic(args.out, json.dumps(doc, indent=2) + "\n")
    print(f"\nwrote {len(models)} models to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
