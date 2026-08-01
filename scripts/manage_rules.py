#!/usr/bin/env python3
"""Manage Nowledge Mem Guidance Rules and Agent Settings in-place via REST API."""
import sys
import os
import json
import urllib.request
import urllib.parse
import urllib.error
import argparse

def load_config():
    config_path = os.path.expanduser('~/.nowledge-mem/config.json')
    config = {
        'apiUrl': 'http://127.0.0.1:14242',
        'apiKey': ''
    }
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'apiUrl' in data:
                    config['apiUrl'] = data['apiUrl'].rstrip('/')
                if 'apiKey' in data:
                    config['apiKey'] = data['apiKey']
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load config from {config_path}: {e}\n")

    env_url = os.environ.get('NMEM_API_URL')
    if env_url:
        config['apiUrl'] = env_url.rstrip('/')
    env_key = os.environ.get('NMEM_API_KEY')
    if env_key:
        config['apiKey'] = env_key

    return config

def make_request(config, path, payload=None, method='GET'):
    url = f"{config['apiUrl'].rstrip('/')}{path}"
    headers = {
        'Content-Type': 'application/json',
        'APP': 'Google Antigravity'
    }
    if config['apiKey']:
        headers['Authorization'] = f"Bearer {config['apiKey']}"
        headers['X-NMEM-API-Key'] = config['apiKey']
        headers['X-MEM-API-Key'] = config['apiKey']

    data_bytes = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

    with urllib.request.urlopen(req, timeout=30) as res:
        res_body = res.read().decode('utf-8')
        return json.loads(res_body) if res_body else {}

def list_rules(config):
    return make_request(config, "/settings/rules", method='GET')

def add_or_update_rule(config, rule_id=None, title=None, content=None, enabled=True):
    payload = {}
    if title:
        payload["title"] = title
    if content:
        payload["content"] = content
    if enabled is not None:
        payload["enabled"] = enabled

    if rule_id:
        path = f"/settings/rules/{urllib.parse.quote(rule_id)}"
        return make_request(config, path, payload=payload, method='PUT')
    else:
        return make_request(config, "/settings/rules", payload=payload, method='POST')

def delete_rule(config, rule_id):
    path = f"/settings/rules/{urllib.parse.quote(rule_id)}"
    return make_request(config, path, method='DELETE')

def main():
    parser = argparse.ArgumentParser(description="Manage Guidance Rules in-place")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform: list, save, delete")

    parser_list = subparsers.add_parser("list", help="List all guidance rules")

    parser_save = subparsers.add_parser("save", help="Create or update a guidance rule in-place")
    parser_save.add_argument("--id", "-i", default=None, help="Rule ID (if updating existing rule)")
    parser_save.add_argument("--title", "-t", required=True, help="Rule title")
    parser_save.add_argument("--content", "-c", required=True, help="Rule markdown content/instructions")
    parser_save.add_argument("--disable", action="store_true", help="Set rule as disabled")

    parser_del = subparsers.add_parser("delete", help="Delete a guidance rule by ID")
    parser_del.add_argument("id", help="Rule ID to delete")

    args = parser.parse_args()
    if not args.action:
        parser.print_help()
        sys.exit(1)

    config = load_config()

    try:
        if args.action == "list":
            rules = list_rules(config)
            print("\n" + "="*50)
            print("📜 Guidance Rules")
            print("="*50)
            print(json.dumps(rules, indent=2))
            print("="*50 + "\n")

        elif args.action == "save":
            res = add_or_update_rule(config, rule_id=args.id, title=args.title, content=args.content, enabled=not args.disable)
            print("\n" + "="*50)
            print("🟢 Guidance Rule Saved Successfully!")
            print("="*50)
            print(json.dumps(res, indent=2))
            print("="*50 + "\n")

        elif args.action == "delete":
            res = delete_rule(config, args.id)
            print("\n" + "="*50)
            print(f"🔴 Guidance Rule '{args.id}' Deleted!")
            print("="*50)

    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8')
        try:
            err_data = json.loads(err_msg)
            message = err_data.get('detail', str(e))
        except Exception:
            message = err_msg or str(e)
        sys.stderr.write(f"API Error (HTTP {e.code}): {message}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Error managing guidance rules: {e}\n")
        sys.exit(1)

if __name__ == '__main__':
    main()
