import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, it } from 'node:test';
import { validateManifest } from '../scripts/validate-plugin.mjs';

function makeValidManifest(overrides = {}) {
  return {
    name: 'example-plugin',
    displayName: 'Example Plugin',
    version: '1.0.0',
    description: 'Example plugin manifest fixture for marketplace validation tests.',
    logo: 'assets/logo.png',
    suggestedPrompts: ['Search project notes'],
    author: { name: 'Example Org', url: 'https://example.com' },
    ...overrides
  };
}

describe('validateManifest marketplace requirements', () => {
  let fixtureRoot;

  before(async () => {
    fixtureRoot = await mkdtemp(path.join(os.tmpdir(), 'nmem-validate-test-'));
    await mkdir(path.join(fixtureRoot, 'assets'), { recursive: true });
    await writeFile(path.join(fixtureRoot, 'assets', 'logo.png'), 'stub');
  });

  after(async () => {
    await rm(fixtureRoot, { recursive: true, force: true });
  });

  it('accepts a complete manifest with object or string author', async () => {
    await validateManifest(makeValidManifest(), fixtureRoot);
    await validateManifest(makeValidManifest({ author: 'Example Org' }), fixtureRoot);
  });

  it('rejects absent, external, or missing logo references', async () => {
    const absentLogo = makeValidManifest();
    delete absentLogo.logo;
    await assert.rejects(() => validateManifest(absentLogo, fixtureRoot), /manifest\.logo/);

    await assert.rejects(
      () => validateManifest(makeValidManifest({ logo: 'https://example.com/logo.png' }), fixtureRoot),
      /relative path/
    );
    await assert.rejects(
      () => validateManifest(makeValidManifest({ logo: '../logo.png' }), fixtureRoot),
      /stay inside/
    );
    await assert.rejects(
      () => validateManifest(makeValidManifest({ logo: 'assets/missing.png' }), fixtureRoot),
      /does not exist/
    );
  });

  it('rejects absent, empty, or oversized suggestedPrompts', async () => {
    const absentPrompts = makeValidManifest();
    delete absentPrompts.suggestedPrompts;
    await assert.rejects(() => validateManifest(absentPrompts, fixtureRoot), /suggestedPrompts/);

    await assert.rejects(
      () => validateManifest(makeValidManifest({ suggestedPrompts: [] }), fixtureRoot),
      /1 to 3/
    );
    await assert.rejects(
      () => validateManifest(makeValidManifest({ suggestedPrompts: ['a', 'b', 'c', 'd'] }), fixtureRoot),
      /1 to 3/
    );
    await assert.rejects(
      () => validateManifest(makeValidManifest({ suggestedPrompts: ['   '] }), fixtureRoot),
      /1 to 3/
    );
  });

  it('rejects absent or empty author attribution', async () => {
    const absentAuthor = makeValidManifest();
    delete absentAuthor.author;
    await assert.rejects(() => validateManifest(absentAuthor, fixtureRoot), /manifest\.author/);

    await assert.rejects(
      () => validateManifest(makeValidManifest({ author: '   ' }), fixtureRoot),
      /manifest\.author/
    );
    await assert.rejects(
      () => validateManifest(makeValidManifest({ author: {} }), fixtureRoot),
      /manifest\.author\.name/
    );
    await assert.rejects(
      () => validateManifest(makeValidManifest({ author: { name: '' } }), fixtureRoot),
      /manifest\.author\.name/
    );
  });
});
