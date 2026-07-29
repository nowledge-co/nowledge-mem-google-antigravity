#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import urllib.error
import time
import argparse
import re

def load_config():
    config_path = os.path.expanduser('~/.nowledge-mem/config.json')
    config = {
        'apiUrl': 'http://127.0.0.1:14242',
        'apiKey': ''
    }
    
    # 1. Config file
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

    # 2. Env vars override
    env_url = os.environ.get('NMEM_API_URL')
    if env_url:
        config['apiUrl'] = env_url.rstrip('/')
    env_key = os.environ.get('NMEM_API_KEY')
    if env_key:
        config['apiKey'] = env_key

    return config

def make_request(config, path, method='GET', body=None):
    url = f"{config['apiUrl']}{path}"
    headers = {
        'Content-Type': 'application/json',
        'APP': 'Google Antigravity'
    }
    if config['apiKey']:
        headers['Authorization'] = f"Bearer {config['apiKey']}"

    data_bytes = None
    if body is not None:
        data_bytes = json.dumps(body).encode('utf-8')

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            res_body = res.read().decode('utf-8')
            return json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
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
            timeout=15
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
            timeout=15
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            raise Exception(result.stderr or f"Exit code {result.returncode}")
    except Exception as e:
        raise Exception(f"CLI fallback failed: {e}")

def compute_trust_badge(skill):
    badge = skill.get('trust_badge') or skill.get('trust_state')
    if badge:
        return str(badge).capitalize()
    passed_tests = skill.get('passed_tests_count') or skill.get('passed_tests') or 0
    if isinstance(passed_tests, list):
        passed_tests = len(passed_tests)
    if passed_tests >= 2:
        return "Proven"
    elif passed_tests >= 1:
        return "Checked"
    stage = skill.get('stage', '')
    if stage == 'active':
        return "Checked"
    return "Draft"

def get_skills_list(config):
    # Fetch all skills from nmem
    try:
        res = make_request(config, '/skills')
        skills = res.get('skills', [])
    except Exception as e:
        sys.stderr.write(f"Warning: REST API failed ({e}). Falling back to CLI...\n")
        try:
            skills = run_cli_list()
        except Exception as cli_err:
            sys.stderr.write(f"Error: {cli_err}\n")
            sys.exit(1)
    # Filter to only show active, candidate, and archived skills
    allowed_stages = {'active', 'candidate', 'archived'}
    filtered = []
    for s in skills:
        if s.get('stage') in allowed_stages:
            s['trust_badge'] = compute_trust_badge(s)
            filtered.append(s)
    return filtered

def list_command(config):
    try:
        skills = get_skills_list(config)
        if not skills:
            print("No skills available to install.")
            return
        
        print(f"{'ID':<20} | {'STAGE':<10} | {'TRUST':<8} | {'TITLE'}")
        print("-" * 80)
        for s in skills:
            title = s.get('title') or s.get('headline') or s.get('id')
            badge = s.get('trust_badge', 'Draft')
            print(f"{s['id']:<20} | {s['stage']:<10} | {badge:<8} | {title}")
    except Exception as e:
        sys.stderr.write(f"Error listing skills: {e}\n")
        sys.exit(1)

def suggest_command(config, workspace_root):
    if not os.path.exists(workspace_root):
        sys.stderr.write(f"Error: Workspace root '{workspace_root}' does not exist.\n")
        sys.exit(1)

    # Gather lowercase workspace terms from files, extensions, and directory names
    workspace_terms = set()
    ignored_dirs = {
        'node_modules', 'venv', '.venv', '.gemini', 'build', 'dist', 
        '.idea', '.vscode', '__pycache__', '.git', 'out', 'target'
    }
    for root, dirs, files in os.walk(workspace_root):
        # Modifying dirs in-place tells os.walk not to visit them
        to_remove = [d for d in dirs if d in ignored_dirs or d.startswith('.')]
        for d in to_remove:
            if d in dirs:
                dirs.remove(d)
            if d == '.git':
                workspace_terms.add('git')
            elif d == '.github':
                workspace_terms.add('github')
                workspace_terms.add('gha')
            else:
                workspace_terms.add(d.lower())

        for d in dirs:
            workspace_terms.add(d.lower())
        for f in files:
            workspace_terms.add(f.lower())
            # Parse extension terms (e.g. cpp, py, md, yaml)
            ext = os.path.splitext(f)[1]
            if ext:
                workspace_terms.add(ext.lower().lstrip('.'))

    suggestions = []

    try:
        skills = get_skills_list(config)
        for s in skills:
            sid = s.get('id', '').lower()
            title = (s.get('title') or s.get('headline') or '').lower()
            desc = (s.get('description') or '').lower()
            
            # Combine skill text to search against
            combined_text = f"{sid} {title} {desc}"
            
            relevance = 0
            reasons = []
            
            for term in workspace_terms:
                if len(term) < 3:  # Skip trivial keywords to reduce noise
                    continue
                # If term exists as a distinct word in the skill manifest, increase relevance
                if re.search(r'\b' + re.escape(term) + r'\b', combined_text):
                    relevance += 3
                    reasons.append(f"'{term}' detected in workspace")

            if relevance > 0:
                suggestions.append({
                    'skill': s,
                    'relevance': relevance,
                    'reasons': list(set(reasons))
                })

        # Sort suggestions by relevance score descending
        suggestions.sort(key=lambda x: x['relevance'], reverse=True)

        if not suggestions:
            print("No matching skills suggested for this workspace based on file patterns.")
            return

        print("Suggested skills for this project:")
        print("-" * 80)
        for sug in suggestions:
            s = sug['skill']
            title = s.get('title') or s.get('headline') or s.get('id')
            reason_str = ", ".join(sug['reasons'])
            badge = s.get('trust_badge', 'Draft')
            print(f"Skill: {title} ({s['id']}) [Stage: {s['stage']}] [Trust: {badge}]")
            print(f"  Reason: {reason_str}")
            print(f"  Description: {s.get('description') or s.get('pitch') or 'N/A'}")
            print()
    except Exception as e:
        sys.stderr.write(f"Error suggesting skills: {e}\n")
        sys.exit(1)

def get_git_dir(workspace_root):
    from pathlib import Path
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

def install_command(config, skill_id, workspace_root, ignore_git):
    try:
        # 1. Fetch skill metadata to check stage
        print(f"Retrieving skill details for '{skill_id}'...")
        use_cli_fallback = False
        try:
            skill = make_request(config, f"/skills/{skill_id}")
            stage = skill.get('stage')
        except Exception as e:
            sys.stderr.write(f"Warning: REST API failed ({e}). Falling back to CLI...\n")
            use_cli_fallback = True
            try:
                skill = run_cli_show(skill_id)
                stage = skill.get('stage')
            except Exception as cli_err:
                sys.stderr.write(f"Error: {cli_err}\n")
                sys.exit(1)
        
        if stage not in {'active', 'candidate', 'archived', 'draft'}:
            sys.stderr.write(f"Error: Skill '{skill_id}' is in stage '{stage}' which is not installable.\n")
            sys.exit(1)

        # 2. Compile if candidate
        if stage == 'candidate':
            if use_cli_fallback:
                # Local CLI does not expose trigger endpoint, warn and proceed
                print("Skill is in 'candidate' stage. CLI fallback cannot trigger REST compilation. Proceeding...")
            else:
                print("Skill is in 'candidate' stage. Compiling skill...")
                try:
                    compile_res = make_request(config, f"/agent/trigger/skill-compile?skill_id={skill_id}", method='POST')
                    print(f"Compilation queued: {compile_res}")
                    
                    # Poll until compiled
                    max_attempts = 12
                    compiled = False
                    for attempt in range(max_attempts):
                        time.sleep(2)
                        check = make_request(config, f"/skills/{skill_id}")
                        if check.get('stage') == 'draft':
                            compiled = True
                            break
                        print(f"Waiting for compilation (attempt {attempt+1}/{max_attempts})...")
                    
                    if not compiled:
                        sys.stderr.write("Error: Skill compilation timed out.\n")
                        sys.exit(1)
                    print("Skill compiled successfully.")
                except Exception as compile_err:
                    sys.stderr.write(f"Warning: Skill compilation failed ({compile_err}). Proceeding to retrieve body via CLI...\n")
                    use_cli_fallback = True

        # 3. Fetch body using include_body=true
        print("Fetching skill markdown body...")
        if use_cli_fallback:
            try:
                skill_details = run_cli_show(skill_id)
                body = skill_details.get('body')
            except Exception as cli_err:
                sys.stderr.write(f"Error retrieving body via CLI: {cli_err}\n")
                sys.exit(1)
        else:
            try:
                skill_details = make_request(config, f"/skills/{skill_id}?include_body=true")
                body = skill_details.get('body')
            except Exception as e:
                sys.stderr.write(f"Warning: REST API failed to get body ({e}). Falling back to CLI...\n")
                try:
                    skill_details = run_cli_show(skill_id)
                    body = skill_details.get('body')
                except Exception as cli_err:
                    sys.stderr.write(f"Error retrieving body via CLI: {cli_err}\n")
                    sys.exit(1)

        if not body:
            sys.stderr.write("Error: Skill body is empty or could not be generated.\n")
            sys.exit(1)

        # 4. Resolve folder name
        # Use name if available, otherwise clean title, fallback to skill_id
        clean_name = skill_details.get('name')
        if not clean_name:
            title = skill_details.get('title') or ""
            clean_name = "".join(c if c.isalnum() or c == '-' else '-' for c in title.lower())
            clean_name = "-".join(filter(None, clean_name.split('-')))
        if not clean_name:
            clean_name = skill_id

        # 5. Write file locally
        target_dir = os.path.join(workspace_root, '.agents', 'skills', clean_name)
        os.makedirs(target_dir, exist_ok=True)
        target_file = os.path.join(target_dir, 'SKILL.md')
        
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(body)
        print(f"Successfully installed/updated skill '{clean_name}' at:")
        print(f"  {target_file}")

        # 6. Git Exclude config
        if ignore_git:
            git_dir = get_git_dir(workspace_root)
            if git_dir and git_dir.exists():
                exclude_path = git_dir / 'info' / 'exclude'
                os.makedirs(exclude_path.parent, exist_ok=True)
                
                # Check if already excluded
                already_excluded = False
                exclude_line = f".agents/skills/{clean_name}/"
                if exclude_path.exists():
                    with open(exclude_path, 'r', encoding='utf-8') as ef:
                        lines = ef.read().splitlines()
                        if any(line.strip() == exclude_line for line in lines):
                            already_excluded = True

                if not already_excluded:
                    with open(exclude_path, 'a', encoding='utf-8') as ef:
                        ef.write(f"\n{exclude_line}\n")
                    print(f"Added local Git exclude for this skill at: {exclude_path}")
                else:
                    print(f"Skill is already excluded in {exclude_path}")
            else:
                print("Info: Workspace root is not a Git repository. Skipping Git exclude configuration.")
    except Exception as e:
        sys.stderr.write(f"Error during installation: {e}\n")
        sys.exit(1)


def restore_merge_command(config, skill_id):
    try:
        print(f"Undoing merge for skill '{skill_id}'...")
        res = make_request(config, f"/skills/{skill_id}/restore-merge", method='POST')
        print("Success: Merge undone.")
        if res:
            print(json.dumps(res, indent=2))
    except Exception as e:
        sys.stderr.write(f"Error restoring merged skill: {e}\n")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Nowledge Mem Skill Manager for Google Antigravity")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # List
    subparsers.add_parser('list', help="List all available skills from Nowledge Mem")

    # Suggest
    suggest_parser = subparsers.add_parser('suggest', help="Analyze workspace files and suggest relevant skills")
    suggest_parser.add_argument('workspace_root', help="Path to workspace root directory")

    # Install
    install_parser = subparsers.add_parser('install', help="Install or update a skill locally in the workspace")
    install_parser.add_argument('skill_id', help="ID of the skill to install")
    install_parser.add_argument('workspace_root', help="Path to workspace root directory")
    install_parser.add_argument('--ignore', action='store_true', help="Ignore the installed skill locally in Git (via .git/info/exclude)")

    # Restore Merge
    restore_parser = subparsers.add_parser('restore-merge', help="Undo a Skill merge and restore the absorbed skill")
    restore_parser.add_argument('skill_id', help="ID of the archived skill to restore")

    args = parser.parse_args()
    config = load_config()

    if args.command == 'list':
        list_command(config)
    elif args.command == 'suggest':
        suggest_command(config, args.workspace_root)
    elif args.command == 'install':
        install_command(config, args.skill_id, args.workspace_root, args.ignore)
    elif args.command == 'restore-merge':
        restore_merge_command(config, args.skill_id)

if __name__ == '__main__':
    main()

