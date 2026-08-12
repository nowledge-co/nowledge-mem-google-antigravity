#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_config():
    env_url = os.environ.get('NMEM_API_URL', '').strip()
    env_key = os.environ.get('NMEM_API_KEY', '').strip() or None
    ignore_host = os.environ.get('NMEM_IGNORE_HOST_CONFIG', '').strip().lower() in ('1', 'true', 'yes')

    config = {
        'apiUrl': env_url.rstrip('/') if env_url else 'http://127.0.0.1:14242',
        'apiKey': env_key or ''
    }

    if not ignore_host:
        custom_cfg = os.environ.get('NMEM_CONFIG_PATH', '').strip()
        config_file = Path(custom_cfg).expanduser() if custom_cfg else Path('~/.nowledge-mem/config.json').expanduser()
        if os.path.exists(config_file):
            try:
                with open(config_file, encoding='utf-8') as f:
                    data = json.load(f)
                    file_url = str(data.get('apiUrl', '')).strip().rstrip('/')
                    file_key = str(data.get('apiKey', '')).strip() or ''

                    if not env_url and file_url:
                        config['apiUrl'] = file_url
                    if not env_key and file_key:
                        if not env_url or env_url.rstrip('/') == file_url:
                            config['apiKey'] = file_key
            except Exception as e:
                if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                    sys.stderr.write(f"Warning: Failed to load config from {config_file}: {e}\n")

    return config


def make_request(config, path, method='GET', body=None):
    url = f"{config['apiUrl']}{path}"
    headers = {
        'Content-Type': 'application/json',
        'APP': 'Google Antigravity'
    }
    if config['apiKey']:
        headers['Authorization'] = f"Bearer {config['apiKey']}"
        headers['X-MEM-API-Key'] = config['apiKey']

    data_bytes = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            res_body = res.read().decode('utf-8')
            return json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            try:
                new_config = load_config()
                config.update(new_config)
                url = f"{config['apiUrl']}{path}"
                headers = {
                    'Content-Type': 'application/json',
                    'APP': 'Google Antigravity'
                }
                if config['apiKey']:
                    headers['Authorization'] = f"Bearer {config['apiKey']}"
                    headers['X-MEM-API-Key'] = config['apiKey']
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=10) as res:
                    res_body = res.read().decode('utf-8')
                    return json.loads(res_body) if res_body else {}
            except Exception:
                pass

        err_msg = e.read().decode('utf-8')
        try:
            err_data = json.loads(err_msg)
            message = err_data.get('detail', str(e))
        except Exception:
            message = err_msg or str(e)
        raise Exception(f"HTTP {e.code}: {message}")
    except Exception as e:
        raise Exception(f"Network error: {e}")

def run_cli_list():
    import subprocess
    try:
        result = subprocess.run(
            ['nmem', 'skills', 'list', '--stage', 'all', '--json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get('skills', [])
        else:
            raise Exception(result.stderr or f"Exit code {result.returncode}")
    except Exception as e:
        raise Exception(f"CLI fallback failed: {e}")

def run_cli_show(skill_id):
    import subprocess
    try:
        result = subprocess.run(
            ['nmem', 'skills', 'show', skill_id, '--json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            raise Exception(result.stderr or f"Exit code {result.returncode}")
    except Exception as e:
        raise Exception(f"CLI show failed: {e}")

def search_skills(query):
    config = load_config()
    encoded_q = urllib.parse.quote(query)
    skills = []

    try:
        data = make_request(config, f"/skills?query={encoded_q}")
        if isinstance(data, dict):
            skills = data.get('skills', [])
        elif isinstance(data, list):
            skills = data
    except Exception as e:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"REST search failed ({e}), trying CLI fallback...\n")
        try:
            skills = run_cli_list()
        except Exception as cli_err:
            sys.stderr.write(f"Search failed: {cli_err}\n")
            return []

    # Filter matching skills by query
    query_lower = query.lower()
    matches = []
    for s in skills:
        s_id = str(s.get('id', '')).lower()
        name = str(s.get('name', '')).lower()
        desc = str(s.get('description', '')).lower()
        if query_lower in s_id or query_lower in name or query_lower in desc:
            matches.append({
                'id': s.get('id'),
                'name': s.get('name') or s.get('id'),
                'description': s.get('description', ''),
                'stage': s.get('stage', 'active')
            })

    if not matches and skills:
        matches = [{'id': s.get('id'), 'name': s.get('name') or s.get('id'), 'description': s.get('description', ''), 'stage': s.get('stage', 'active')} for s in skills[:5]]

    return matches

def fetch_skill(skill_id):
    config = load_config()
    try:
        data = make_request(config, f"/skills/{urllib.parse.quote(skill_id)}?include_body=true")
        if data:
            return data
    except Exception as e:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"REST fetch failed ({e}), trying CLI fallback...\n")
        try:
            return run_cli_show(skill_id)
        except Exception as cli_err:
            raise Exception(f"Fetch failed: {cli_err}")

    return {}

def get_git_dir(workspace_root):
    git_path = Path(workspace_root) / ".git"
    if not git_path.exists():
        return None
    if git_path.is_dir():
        return git_path
    if git_path.is_file():
        try:
            content = git_path.read_text(encoding='utf-8').strip()
            if content.startswith("gitdir:"):
                gitdir_path = content.split("gitdir:", 1)[1].strip()
                resolved_path = Path(gitdir_path)
                if not resolved_path.is_absolute():
                    resolved_path = (git_path.parent / resolved_path).resolve()
                return resolved_path
        except Exception:
            pass
    return None

def install_skill(skill_id, workspace_root, ignore=False):
    data = fetch_skill(skill_id)
    body = data.get('body') or data.get('content') or data.get('markdown') or ""
    if not body and isinstance(data.get('skill'), dict):
        body = data['skill'].get('body') or data['skill'].get('content') or ""

    if not body:
        raise Exception(f"Skill '{skill_id}' has no body/content")

    skill_folder = re.sub(r'[^a-zA-Z0-9_-]', '-', skill_id).strip('-').lower()
    target_dir = Path(workspace_root) / ".agents" / "skills" / skill_folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "SKILL.md"

    target_file.write_text(body, encoding='utf-8')

    if ignore:
        git_dir = get_git_dir(workspace_root)
        if git_dir and git_dir.exists():
            exclude_file = git_dir / "info" / "exclude"
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            rel_entry = f".agents/skills/{skill_folder}/"
            current_exclude = ""
            if exclude_file.exists():
                try:
                    current_exclude = exclude_file.read_text(encoding='utf-8')
                except Exception:
                    pass
            if rel_entry not in current_exclude:
                with open(exclude_file, 'a', encoding='utf-8') as f:
                    f.write(f"\n{rel_entry}\n")

    return {
        'status': 'success',
        'skill_id': skill_id,
        'path': str(target_file),
        'ignored': ignore
    }


def main():
    parser = argparse.ArgumentParser(description="Nowledge Mem On-Demand Skill Loader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = subparsers.add_parser("search")
    p_search.add_argument("query", help="Skill search query")

    # fetch
    p_fetch = subparsers.add_parser("fetch")
    p_fetch.add_argument("skill_id", help="Skill ID to fetch body for")

    # install
    p_install = subparsers.add_parser("install")
    p_install.add_argument("skill_id", help="Skill ID to install")
    p_install.add_argument("workspace_root", help="Target workspace root path")
    p_install.add_argument("--ignore", action="store_true", help="Add to .git/info/exclude")

    args = parser.parse_args()

    if args.command == "search":
        matches = search_skills(args.query)
        print(json.dumps(matches, indent=2))
    elif args.command == "fetch":
        data = fetch_skill(args.skill_id)
        print(json.dumps(data, indent=2))
    elif args.command == "install":
        res = install_skill(args.skill_id, args.workspace_root, ignore=args.ignore)
        print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
