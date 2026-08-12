#!/usr/bin/env python3
"""Update Working Memory in-place via Nowledge Mem REST API (PUT /agent/working-memory)."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
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


def update_working_memory(config, content, space_id=None):
    url = f"{config['apiUrl'].rstrip('/')}/agent/working-memory"
    if space_id:
        url += f"?space_id={urllib.parse.quote(space_id)}"

    headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
    if config["apiKey"]:
        headers["Authorization"] = f"Bearer {config['apiKey']}"
        headers["X-NMEM-API-Key"] = config["apiKey"]
        headers["X-MEM-API-Key"] = config["apiKey"]

    payload = {"content": content}
    if space_id:
        payload["space_id"] = space_id

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="PUT")

    with urllib.request.urlopen(req, timeout=30) as res:
        res_body = res.read().decode("utf-8")
        return json.loads(res_body) if res_body else {}


def main():
    parser = argparse.ArgumentParser(description="Update Nowledge Mem Working Memory in-place")
    parser.add_argument("content_path", nargs="?", help="Path to markdown file with new working memory content")
    parser.add_argument("--content", "-c", default=None, help="Raw string content if no file path is provided")
    parser.add_argument("--space-id", default=None, help="Optional space ID guard")
    args = parser.parse_args()

    content = ""
    if args.content_path:
        if not os.path.exists(args.content_path):
            sys.stderr.write(f"Error: File '{args.content_path}' does not exist.\n")
            sys.exit(1)
        with open(args.content_path, encoding="utf-8") as f:
            content = f.read()
    elif args.content:
        content = args.content
    else:
        sys.stderr.write("Error: Must provide a file path or --content string.\n")
        sys.exit(1)

    config = load_config()
    print("Updating Working Memory in-place...")
    try:
        data = update_working_memory(config, content, space_id=args.space_id)
        size = data.get("size_bytes", len(content))
        focus = data.get("focus_areas", 0)
        print("\n" + "=" * 50)
        print("🟢 Working Memory Successfully Updated!")
        print("=" * 50)
        print(f"Size:        {size} bytes")
        print(f"Focus Areas: {focus}")
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
        sys.stderr.write(f"Error updating Working Memory: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
