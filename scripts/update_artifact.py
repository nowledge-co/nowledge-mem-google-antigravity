#!/usr/bin/env python3
"""Update Library Artifact content and metadata in-place via Nowledge Mem REST API."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request


def load_config():
    config_path = os.path.expanduser("~/.nowledge-mem/config.json")
    config = {"apiUrl": "http://127.0.0.1:14242", "apiKey": ""}

    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
                if "apiUrl" in data:
                    config["apiUrl"] = data["apiUrl"].rstrip("/")
                if "apiKey" in data:
                    config["apiKey"] = data["apiKey"]
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load config from {config_path}: {e}\n")

    env_url = os.environ.get("NMEM_API_URL")
    if env_url:
        config["apiUrl"] = env_url.rstrip("/")
    env_key = os.environ.get("NMEM_API_KEY")
    if env_key:
        config["apiKey"] = env_key

    return config


def extract_frontmatter_field(markdown_text, field_names):
    match = re.search(r"^---\s*\n(.*?)\n---", markdown_text, re.DOTALL)
    if not match:
        return None
    fm = match.group(1)
    for line in fm.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            val = val.strip().strip('"').strip("'")
            if key in field_names:
                return val
    return None


def update_artifact_content(config, artifact_id, content, space_id=None):
    url = f"{config['apiUrl'].rstrip('/')}/sources/{artifact_id}/content"
    if space_id:
        url += f"?space_id={urllib.parse.quote(space_id)}"

    headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
    if config["apiKey"]:
        headers["Authorization"] = f"Bearer {config['apiKey']}"
        headers["X-NMEM-API-Key"] = config["apiKey"]
        headers["X-MEM-API-Key"] = config["apiKey"]

    payload = {"content": content}
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="PUT")

    with urllib.request.urlopen(req, timeout=30) as res:
        res_body = res.read().decode("utf-8")
        return json.loads(res_body) if res_body else {}


def update_artifact_reparse(config, artifact_id, space_id=None):
    url = f"{config['apiUrl'].rstrip('/')}/sources/{artifact_id}"
    if space_id:
        url += f"?space_id={urllib.parse.quote(space_id)}"

    headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
    if config["apiKey"]:
        headers["Authorization"] = f"Bearer {config['apiKey']}"
        headers["X-NMEM-API-Key"] = config["apiKey"]
        headers["X-MEM-API-Key"] = config["apiKey"]

    payload = {"action": "reparse"}
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="PATCH")

    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            res_body = res.read().decode("utf-8")
            return json.loads(res_body) if res_body else {}
    except Exception as e:
        sys.stderr.write(f"Notice: Reparse trigger warning ({e}).\n")
        return {}


def search_existing_artifact(config, title):
    if not title:
        return None
    try:
        url = f"{config['apiUrl'].rstrip('/')}/sources/search?q={urllib.parse.quote(title)}"
        headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
        if config["apiKey"]:
            headers["Authorization"] = f"Bearer {config['apiKey']}"
            headers["X-NMEM-API-Key"] = config["apiKey"]
            headers["X-MEM-API-Key"] = config["apiKey"]

        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
            sources = data.get("sources", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

            target_slug = title.strip().lower()
            for s in sources:
                s_title = (s.get("title") or "").strip().lower()
                if s_title == target_slug:
                    return s
    except Exception as e:
        sys.stderr.write(f"Notice: Artifact search warning ({e}).\n")
    return None


def main():
    parser = argparse.ArgumentParser(description="Update a Nowledge Mem Library Artifact in-place")
    parser.add_argument("artifact_path", help="Path to markdown file containing artifact content")
    parser.add_argument("--artifact-id", "-i", default=None, help="Target artifact/source ID (e.g. src_a1b2c3d4)")
    parser.add_argument("--title", "-t", default=None, help="Optional updated title")
    parser.add_argument("--space-id", default=None, help="Optional space ID")
    parser.add_argument("--reparse", action="store_true", help="Trigger source reparse after content update")
    args = parser.parse_args()

    if not os.path.exists(args.artifact_path):
        sys.stderr.write(f"Error: File '{args.artifact_path}' does not exist.\n")
        sys.exit(1)

    try:
        with open(args.artifact_path, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        sys.stderr.write(f"Error reading '{args.artifact_path}': {e}\n")
        sys.exit(1)

    artifact_id = args.artifact_id or extract_frontmatter_field(content, ("id", "artifact_id", "source_id"))
    title = args.title or extract_frontmatter_field(content, ("title", "name"))
    config = load_config()

    if not artifact_id and title:
        existing = search_existing_artifact(config, title)
        if existing:
            artifact_id = existing.get("id") or existing.get("source_id")
            print(
                f"Notice: Found existing artifact '{existing.get('title')}' ({artifact_id}). Auto-binding for in-place update."
            )

    if not artifact_id:
        sys.stderr.write(
            "Error: Could not determine target artifact ID. Specify --artifact-id or include 'id: <artifact_id>' in frontmatter.\n"
        )
        sys.exit(1)

    print(f"Updating Library Artifact '{artifact_id}' in-place...")
    try:
        res_content = update_artifact_content(config, artifact_id, content, space_id=args.space_id)
        print(f"Content updated for artifact '{artifact_id}'.")

        if args.reparse:
            update_artifact_reparse(config, artifact_id, space_id=args.space_id)
            print(f"Reparse triggered for artifact '{artifact_id}'.")

        print("\n" + "=" * 50)
        print("🟢 Library Artifact Successfully Updated In-Place!")
        print("=" * 50)
        print(f"Artifact ID: {artifact_id}")
        print("=" * 50 + "\n")

    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        try:
            err_data = json.loads(err_msg)
            message = err_data.get("detail", str(e))
        except Exception:
            message = err_msg or str(e)
        sys.stderr.write(f"API Error (HTTP {e.code}): {message}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Error during artifact update: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
