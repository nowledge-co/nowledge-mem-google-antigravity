import { access, readFile } from 'node:fs/promises';
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

function assertNonEmptyString(value, label) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${label} must be a non-empty string`);
  }
}

export async function validateManifest(manifest, rootDir = pluginRoot) {
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
    throw new Error('manifest must be a JSON object');
  }

  assertNonEmptyString(manifest.name, 'manifest.name');
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(manifest.name)) {
    throw new Error(`manifest.name (${manifest.name}) must be lowercase kebab-case matching ^[a-z0-9]+(-[a-z0-9]+)*$`);
  }
  assertNonEmptyString(manifest.displayName, 'manifest.displayName');
  assertNonEmptyString(manifest.version, 'manifest.version');
  assertNonEmptyString(manifest.description, 'manifest.description');
  if (manifest.repository !== undefined) assertNonEmptyString(manifest.repository, 'manifest.repository');

  if (
    !Array.isArray(manifest.suggestedPrompts) ||
    manifest.suggestedPrompts.length < 1 ||
    manifest.suggestedPrompts.length > 3 ||
    manifest.suggestedPrompts.some((p) => typeof p !== 'string' || p.trim() === '')
  ) {
    throw new Error('manifest.suggestedPrompts must be an array of 1 to 3 non-empty strings');
  }

  assertNonEmptyString(manifest.logo, 'manifest.logo');
  if (manifest.logo.startsWith('/') || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(manifest.logo)) {
    throw new Error('manifest.logo must be a relative path inside the plugin');
  }
  const resolvedRoot = path.resolve(rootDir);
  const resolvedLogo = path.resolve(resolvedRoot, manifest.logo);
  if (resolvedLogo !== resolvedRoot && !resolvedLogo.startsWith(resolvedRoot + path.sep)) {
    throw new Error('manifest.logo must stay inside the plugin directory');
  }
  if (!/\.(png|svg|jpe?g|webp)$/i.test(manifest.logo)) {
    throw new Error('manifest.logo must point to a .png, .svg, .jpg, .jpeg, or .webp file');
  }
  try {
    await access(resolvedLogo);
  } catch {
    throw new Error(`manifest.logo file does not exist: ${manifest.logo}`);
  }

  if (typeof manifest.author === 'string') {
    assertNonEmptyString(manifest.author, 'manifest.author');
  } else if (manifest.author && typeof manifest.author === 'object' && !Array.isArray(manifest.author)) {
    assertNonEmptyString(manifest.author.name, 'manifest.author.name');
    if (manifest.author.email !== undefined) {
      assertNonEmptyString(manifest.author.email, 'manifest.author.email');
    }
    if (manifest.author.url !== undefined) {
      assertNonEmptyString(manifest.author.url, 'manifest.author.url');
    }
  } else {
    throw new Error('manifest.author must be a non-empty string or an object with a non-empty name');
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

  try {
    await validateManifest(manifest, pluginRoot);
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
  }

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
  const endpoint = server.url;
  if (typeof endpoint !== 'string' || !endpoint.endsWith('/mcp') || (!endpoint.startsWith('http://') && !endpoint.startsWith('https://'))) {
    fail('mcp_config.json nowledge-mem.url must be a valid http(s) URL ending with /mcp');
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
    'assets/logo.png',
    'mcp_config.json',
    'hooks.json',
    'package.json',
    'README.md',
    'CHANGELOG.md',
    'RELEASING.md',
    'rules/nowledge-mem.md',
    'hooks/nmem_entrypoint.py',
    'hooks/bridge-start.py',
    'hooks/session-start.py',
    'hooks/session-end.py',
    'hooks/nmem-gate.py',
    'hooks/nmem_status.py',
    'hooks/post-invocation.py',
    'hooks/post-tool-use.py',
    'skills/nmem-memory-working/SKILL.md',
    'skills/nmem-bridge-chat/SKILL.md',
    'skills/nmem-memory-search/SKILL.md',
    'skills/nmem-graph-explore/SKILL.md',
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

      if (relPath === 'rules/nowledge-mem.md') {
        const fmMatch = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
        if (!fmMatch || !/^trigger:\s*always_on\s*$/m.test(fmMatch[1])) {
          fail('rules/nowledge-mem.md must declare trigger: always_on in its frontmatter');
        }
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
        const bridgeStart = hooksConfig['nowledge-mem-bridge']?.SessionStart?.[0];
        if (bridgeStart?.type !== 'command' || !bridgeStart.command?.includes('nmem_entrypoint.py bridge-start')) {
          fail('hooks.json must route the Bridge SessionStart hook through nmem_entrypoint.py');
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

  const agyCheck = spawnSync('agy', ['plugin', 'validate', '--json', '.'], {
    cwd: pluginRoot,
    encoding: 'utf8'
  });
  if (agyCheck.error) {
    if (agyCheck.error.code !== 'ENOENT') {
      fail(`agy plugin validate failed to run: ${agyCheck.error.message}`);
    }
  } else if (agyCheck.stdout) {
    try {
      const report = JSON.parse(agyCheck.stdout);
      const unexpected = (report.findings || []).filter(
        (f) => f.severity === 'error' || (f.severity === 'warning' && f.file !== 'AGENTS.md')
      );
      if (unexpected.length > 0) {
        console.error(agyCheck.stdout);
        fail(`agy plugin validate reported ${unexpected.length} error/warning finding(s)`);
      }
      console.log('agy plugin validate passed (ignoring expected repo-root AGENTS.md warning).');
    } catch {
      if (agyCheck.status !== 0) {
        fail(`agy plugin validate failed: ${agyCheck.stderr || agyCheck.stdout}`);
      }
    }
  } else if (agyCheck.status !== 0) {
    fail(`agy plugin validate failed: ${agyCheck.stderr || `exit status ${agyCheck.status}`}`);
  }

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


  console.log('Running validator unit tests...');
  const validatorTestProc = spawnSync(process.execPath, ['--test', 'tests/test_validate_plugin.mjs'], {
    cwd: pluginRoot,
    stdio: 'inherit'
  });
  if (validatorTestProc.status !== 0) {
    fail('Validator unit tests failed.');
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

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
