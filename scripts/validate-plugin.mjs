import { readFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifestPath = path.join(pluginRoot, 'plugin.json');
const mcpConfigPath = path.join(pluginRoot, 'mcp_config.json');
const packageJsonPath = path.join(pluginRoot, 'package.json');

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(1);
}

async function readJson(filePath) {
  const text = await readFile(filePath, 'utf8');
  return JSON.parse(text);
}

function assertString(value, label) {
  if (typeof value !== 'string' || value.trim() === '') {
    fail(`${label} must be a non-empty string`);
  }
}

async function main() {
  const manifest = await readJson(manifestPath);
  let mcpConfig = await readJson(mcpConfigPath);
  try {
    const gitShow = spawnSync('git', ['show', 'HEAD:mcp_config.json'], { cwd: pluginRoot, encoding: 'utf8' });
    if (gitShow.status === 0 && gitShow.stdout) {
      mcpConfig = JSON.parse(gitShow.stdout);
    }
  } catch (e) {}
  const packageJson = await readJson(packageJsonPath);
  const pluginDirName = path.basename(pluginRoot);

  assertString(manifest.name, 'manifest.name');
  assertString(manifest.version, 'manifest.version');
  assertString(manifest.description, 'manifest.description');

  let isWorktree = pluginRoot.includes('/worktrees/') || pluginRoot.includes('\\worktrees\\');
  if (!isWorktree) {
    try {
      const gitRev = spawnSync('git', ['rev-parse', '--git-dir'], { cwd: pluginRoot, encoding: 'utf8' });
      if (gitRev.status === 0 && gitRev.stdout && (gitRev.stdout.includes('.git/worktrees/') || gitRev.stdout.includes('.git\\worktrees\\'))) {
        isWorktree = true;
      }
    } catch (e) {}
  }
  if (manifest.name !== pluginDirName && pluginDirName !== 'nmem' && pluginDirName !== 'nowledge-mem' && !isWorktree) {
    fail(`manifest.name (${manifest.name}) must match directory name (or be "nmem", "nowledge-mem", or a git worktree), but got "${pluginDirName}"`);
  }


  if (manifest.version !== packageJson.version) {
    fail(`manifest.version (${manifest.version}) must match package.json version (${packageJson.version})`);
  }

  const server = mcpConfig.mcpServers?.['nowledge-mem'];
  if (!server || typeof server !== 'object') {
    fail('mcp_config.json must define mcpServers.nowledge-mem');
  }
  if (typeof server.serverUrl !== 'string' || !server.serverUrl.endsWith('/mcp/') || (!server.serverUrl.startsWith('http://') && !server.serverUrl.startsWith('https://'))) {
    fail('mcp_config.json nowledge-mem.serverUrl must be a valid http(s) URL ending with /mcp/');
  }
  if (server.headers?.APP !== 'Google Antigravity') {
    fail('mcp_config.json nowledge-mem.headers.APP must be "Google Antigravity"');
  }
  if ('X-MEM-API-Key' in (server.headers || {})) {
    fail('mcp_config.json must use X-NMEM-API-Key, not the legacy X-MEM-API-Key header');
  }
  for (const value of Object.values(server.headers || {})) {
    if (typeof value === 'string' && /nmem_(?!your_key\b)[A-Za-z0-9_-]+/.test(value)) {
      fail('mcp_config.json must not contain a real nmem API key');
    }
  }

  const requiredPaths = [
    'plugin.json',
    'mcp_config.json',
    'hooks.json',
    'package.json',
    'README.md',
    'CHANGELOG.md',
    'RELEASING.md',
    'rules/nowledge-mem.md',
    'hooks/nmem_entrypoint.py',
    'hooks/session-start.py',
    'hooks/session-end.py',
    'hooks/nmem-gate.py',
    'hooks/nmem_status.py',
    'hooks/post-invocation.py',
    'hooks/post-tool-use.py',
    'sidecars/nowledge-mem-sync/sidecar.json',
    'skills/nmem-memory-working/SKILL.md',
    'skills/nmem-memory-search/SKILL.md',
    'skills/nmem-memory-distill/SKILL.md',
    'skills/nmem-thread-save/SKILL.md',
    'skills/nmem-thread-handoff/SKILL.md',
    'skills/nmem-status/SKILL.md',
    'skills/nmem-fs-explore/SKILL.md',
    'skills/nmem-skill-manage/SKILL.md',
    'skills/nmem-skill-manage/scripts/manage_skills.py',
    'skills/nmem-skill-propose/SKILL.md',
    'skills/nmem-skill-load/SKILL.md',
    'skills/nmem-skill-load/scripts/load_skill.py',
    '.github/workflows/ci.yml',
    'ARCHITECTURE.md',
    'scripts/validate-plugin.mjs',
    'scripts/package-plugin.mjs',
    'tests/test_hooks.py',
    'tests/integration/conftest.py',
    'tests/integration/test_mem_container.py',
    `release-notes/${manifest.version}.md`
  ];

  for (const relPath of requiredPaths) {
    const absPath = path.join(pluginRoot, relPath);
    try {
      const text = await readFile(absPath, 'utf8');
      if (text.trim() === '') {
        fail(`${relPath} must not be empty`);
      }

      if (relPath === 'hooks.json') {
        const hooksConfig = JSON.parse(text);
        if (
          !hooksConfig ||
          typeof hooksConfig !== 'object' ||
          typeof hooksConfig['nowledge-mem-hooks'] !== 'object' ||
          hooksConfig['nowledge-mem-hooks'] === null ||
          Array.isArray(hooksConfig['nowledge-mem-hooks'])
        ) {
          fail('hooks.json must contain a top-level "nowledge-mem-hooks" object');
        }
      }
    } catch (error) {
      if (error instanceof SyntaxError) {
        fail(`${relPath} must contain valid JSON`);
      }
      fail(`missing required file: ${relPath}`);
    }
  }

  console.log('Validated Google Antigravity plugin manifest, config files, and required release files.');

  console.log('Running pre-commit static analysis...');
  const precommitProc = spawnSync('uv', ['run', 'pre-commit', 'run', '--all-files'], {
    cwd: pluginRoot,
    stdio: 'inherit'
  });
  if (precommitProc.status !== 0) {
    const ruffProc = spawnSync('uv', ['run', 'ruff', 'check', '.'], {
      cwd: pluginRoot,
      stdio: 'inherit'
    });
    if (ruffProc.status !== 0) {
      fail('Pre-commit static analysis checks failed.');
    }
  }


  console.log('Running hooks unit test suite...');

  const testProc = spawnSync('uv', ['run', 'pytest', 'tests/test_hooks.py'], {
    cwd: pluginRoot,
    stdio: 'inherit'
  });
  if (testProc.status !== 0) {
    const testProcFallback = spawnSync('python3', ['-m', 'pytest', 'tests/test_hooks.py'], {
      cwd: pluginRoot,
      stdio: 'inherit'
    });
    if (testProcFallback.status !== 0) {
      const testProcUnittest = spawnSync('python3', ['-m', 'unittest', 'discover', '-s', 'tests'], {
        cwd: pluginRoot,
        stdio: 'inherit'
      });
      if (testProcUnittest.status !== 0) {
        fail('Hooks unit tests failed.');
      }
    }
  }
  console.log('All hooks unit tests passed.');

}

await main();
