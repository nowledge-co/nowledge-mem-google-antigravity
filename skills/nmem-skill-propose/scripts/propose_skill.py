#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import urllib.error
import re
import argparse
import subprocess

def load_config():
    config_path = os.path.expanduser('~/.nowledge-mem/config.json')
    config = {
        'apiUrl': 'http://127.0.0.1:14242',
        'apiKey': ''
    }
    
    # 1. Load from config file
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

    # 2. Environment variables override
    env_url = os.environ.get('NMEM_API_URL')
    if env_url:
        config['apiUrl'] = env_url.rstrip('/')
    env_key = os.environ.get('NMEM_API_KEY')
    if env_key:
        config['apiKey'] = env_key

    return config

def get_endpoint_url(config, path):
    api_url = config['apiUrl'].rstrip('/')
    is_loopback = any(x in api_url for x in ['127.0.0.1', 'localhost', '::1'])
    
    if not is_loopback and not api_url.endswith('/remote-api'):
        return f"{api_url}/remote-api{path}"
    else:
        return f"{api_url}{path}"

def extract_frontmatter_field(skill_md, field_names):
    match = re.search(r'^---\s*\n(.*?)\n---', skill_md, re.DOTALL)
    if not match:
        return None
    fm = match.group(1)
    for line in fm.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            key, val = line.split(':', 1)
            key = key.strip().lower()
            val = val.strip().strip('"').strip("'")
            if key in field_names:
                return val
    return None

def make_request(config, path, payload=None, method='POST'):
    url = get_endpoint_url(config, path)
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

def find_matching_existing_skill(config, target_name):
    if not target_name:
        return None
    try:
        skills_url = f"{config['apiUrl'].rstrip('/')}/skills"
        headers = {
            'Content-Type': 'application/json',
            'APP': 'Google Antigravity'
        }
        if config['apiKey']:
            headers['Authorization'] = f"Bearer {config['apiKey']}"
            headers['X-NMEM-API-Key'] = config['apiKey']
            headers['X-MEM-API-Key'] = config['apiKey']

        req = urllib.request.Request(skills_url, headers=headers, method='GET')
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode('utf-8'))
            skills = data.get('skills', [])

        target_slug = target_name.strip().lower().replace('_', '-').replace(' ', '-')
        
        active_matches = []
        other_matches = []

        for s in skills:
            sid = s.get('id', '')
            s_name = (s.get('name') or s.get('title') or '').strip().lower().replace('_', '-').replace(' ', '-')
            s_stage = s.get('stage', '')
            if s_stage in ('archived', 'rejected'):
                continue

            if sid == target_name or s_name == target_slug or s_name.replace('-', '') == target_slug.replace('-', ''):
                if s_stage == 'active':
                    active_matches.append(s)
                else:
                    other_matches.append(s)

        if active_matches:
            return active_matches[0]
        elif other_matches:
            return other_matches[0]
            
    except Exception as e:
        sys.stderr.write(f"Notice: Existing skill check warning ({e}).\n")
    return None

def activate_skill(config, skill_id):
    if not skill_id or skill_id == 'unknown':
        return False
    try:
        act_res = make_request(config, f"/skills/{skill_id}/activate", payload={}, method='POST')
        print(f"🟢 Activated skill '{skill_id}' on Nowledge Mem.")
        return True
    except Exception as e:
        sys.stderr.write(f"Notice: REST API activation notice ({e}). Trying CLI fallback...\n")
        try:
            cmd = ['nmem', 'skills', 'activate', '-y', skill_id]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                print(f"🟢 Activated skill '{skill_id}' via nmem CLI.")
                return True
            else:
                sys.stderr.write(f"Warning: CLI activation failed: {res.stderr.strip()}\n")
        except Exception as ex:
            sys.stderr.write(f"Warning: Failed to activate skill '{skill_id}': {ex}\n")
        return False

def main():
    parser = argparse.ArgumentParser(description="Propose or Update a Nowledge Mem Skill")
    parser.add_argument("draft_path", help="Path to the skill draft markdown file")
    parser.add_argument("--skill-id", "-s", default=None, help="Target skill ID to update in-place")
    parser.add_argument("--create-new", action="store_true", help="Force creation of a new skill even if a name match exists")
    parser.add_argument("--force", "-f", action="store_true", help="Force import / overwrite")
    parser.add_argument("--no-apply", action="store_true", help="Stage edit-body without applying version immediately")
    parser.add_argument("--activate", "-a", dest="should_activate", action="store_true", default=True, help="Activate skill on Nowledge Mem after proposal/import or update (default)")
    parser.add_argument("--no-activate", dest="should_activate", action="store_false", help="Keep skill in proposal stage on Nowledge Mem without activating")
    args = parser.parse_args()

    draft_path = args.draft_path
    if not os.path.exists(draft_path):
        sys.stderr.write(f"Error: Draft file '{draft_path}' does not exist.\n")
        sys.exit(1)

    try:
        with open(draft_path, 'r', encoding='utf-8') as f:
            skill_md = f.read()
    except Exception as e:
        sys.stderr.write(f"Error: Failed to read '{draft_path}': {e}\n")
        sys.exit(1)

    if not skill_md.strip().startswith('---'):
        sys.stderr.write("Warning: The draft file does not seem to start with frontmatter (---).\n")

    skill_id = args.skill_id or extract_frontmatter_field(skill_md, ('id', 'skill_id', 'skill-id'))
    skill_name = extract_frontmatter_field(skill_md, ('name', 'title'))
    config = load_config()

    # Pre-creation check for existing matching skill if skill_id is missing and not --create-new
    if not skill_id and not args.create_new and skill_name:
        existing = find_matching_existing_skill(config, skill_name)
        if existing:
            skill_id = existing['id']
            print(f"Notice: Found existing skill '{existing.get('title') or skill_name}' ({skill_id}) [stage: {existing.get('stage')}].")
            print("Auto-binding request to perform in-place update. (Pass --create-new to force creation of a new skill).")

    if skill_id:
        # In-Place Skill Update Path
        print(f"Detected target skill ID '{skill_id}'. Performing in-place update...")
        try:
            edit_res = make_request(config, "/agent/skill-builder/edit-body", {
                "skill_id": skill_id,
                "body": skill_md
            })
            print(f"Staged edit-body for skill '{skill_id}'.")
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8')
            if e.code == 409 and "identical" in err_msg:
                print(f"Notice: Skill '{skill_id}' body is identical to current version. No edits to stage.")
            else:
                try:
                    err_data = json.loads(err_msg)
                    message = err_data.get('detail', str(e))
                except Exception:
                    message = err_msg or str(e)
                sys.stderr.write(f"API Error during edit-body (HTTP {e.code}): {message}\n")
                sys.exit(1)
        except Exception as e:
            sys.stderr.write(f"Error during edit-body: {e}\n")
            sys.exit(1)

        version = "updated"
        if not args.no_apply:
            try:
                apply_url = f"{config['apiUrl'].rstrip('/')}/skills/{skill_id}/apply-version"
                headers = {
                    'Content-Type': 'application/json',
                    'APP': 'Google Antigravity'
                }
                if config['apiKey']:
                    headers['Authorization'] = f"Bearer {config['apiKey']}"
                    headers['X-NMEM-API-Key'] = config['apiKey']
                    headers['X-MEM-API-Key'] = config['apiKey']

                req_apply = urllib.request.Request(apply_url, data=b"{}", headers=headers, method='POST')
                with urllib.request.urlopen(req_apply, timeout=30) as res_apply:
                    apply_data = json.loads(res_apply.read().decode('utf-8'))
                    version = apply_data.get('version', apply_data.get('skill', {}).get('version', 'updated'))
                print(f"Applied version v{version} to skill '{skill_id}'.")
            except Exception as e:
                sys.stderr.write(f"Warning: Failed to apply version ({e}). Staged version remains pending.\n")

        activated = False
        if args.should_activate:
            activated = activate_skill(config, skill_id)

        print("\n" + "="*50)
        print("🟢 Skill Successfully Updated In-Place!")
        print("="*50)
        print(f"Skill ID:    {skill_id}")
        print(f"Status:      Updated (v{version})")
        print(f"Stage:       {'active' if activated else 'proposal/staged'}")
        print("="*50 + "\n")

    else:
        # New Skill Creation / Import Path
        print("No existing skill ID detected. Creating new skill via import...")
        try:
            response_data = make_request(config, "/agent/skill-builder/import", {
                'skill_md': skill_md,
                'force': args.force
            })
            
            created = response_data.get('created', False)
            skill = response_data.get('skill', {})
            matched = response_data.get('matched', {})
            new_skill_id = skill.get('id') or matched.get('id') or 'unknown'
            s_name = skill.get('name') or skill.get('title') or skill_name or 'unknown'
            stage = skill.get('stage', 'proposal')

            if not created and matched and matched.get('id'):
                # Backend detected an existing skill match when force=False
                matched_id = matched['id']
                print(f"Notice: Import matched existing skill '{matched_id}'. Transitioning to in-place update...")
                edit_res = make_request(config, "/agent/skill-builder/edit-body", {
                    "skill_id": matched_id,
                    "body": skill_md
                })
                if not args.no_apply:
                    apply_url = f"{config['apiUrl'].rstrip('/')}/skills/{matched_id}/apply-version"
                    make_request(config, f"/skills/{matched_id}/apply-version", payload={}, method='POST')
                new_skill_id = matched_id
                stage = matched.get('stage', 'active')

            activated = False
            if args.should_activate and new_skill_id != 'unknown':
                activated = activate_skill(config, new_skill_id)
                if activated:
                    stage = 'active'

            print("\n" + "="*50)
            print("🟢 Skill Proposal Successfully Processed!")
            print("="*50)
            print(f"Skill ID:    {new_skill_id}")
            print(f"Name:        {s_name}")
            print(f"Stage:       {stage}")
            print(f"Status:      {'Created' if created else 'Updated'}")
            print("-"*50)
            if not activated:
                print("To make this skill live to your AI tools, run:")
                print(f"  nmem skills activate -y {new_skill_id}")
            else:
                print("Skill is now ACTIVE and available to all connected AI agents.")
            print("="*50 + "\n")

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
            sys.stderr.write(f"Connection Error: {e}\n")
            sys.exit(1)

if __name__ == '__main__':
    main()
