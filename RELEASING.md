# Releasing the Google Antigravity Plugin

This repository is dedicated to the Google Antigravity integration, so the repository root itself is the plugin root for local installs, discovery, and tagged releases.

## Why This Release Path

Antigravity's release docs require `plugin.json` to be at the root of the repository **or the release archive**.

In this repo, the repository root is already the plugin root. That means:
- local development uses workspace-level linking or global linking in `~/.gemini/config/plugins/`
- public release and discovery can both inspect the repository root directly
- the packaged archive stays useful as a clean GitHub Release install artifact

## Manual Prerequisites

These are required for Marketplace submission and discovery:
- the GitHub repository must be public
- the repository About section must include the `google-antigravity-plugin` topic
- `plugin.json` must include `name`, `displayName`, `description`, `version`, `logo` (bundled 1:1 square icon under `assets/`), `suggestedPrompts` (up to 3), and `author`
- the plugin must pass `agy plugin validate` with zero errors (and `agy plugin validate --strict dist/release-root` on the built archive)
- initial Marketplace listing is submitted via the [Antigravity Marketplace partner intake form](https://forms.gle/2EX5RFYPoJe1UgxR9) (there is no `agy plugin publish` CLI or API)
- the release must be tagged and published on GitHub
- the attached archive must contain `plugin.json` at the archive root
- the attached archive must be free of macOS AppleDouble metadata entries such as `._README.md`

## Validate Locally

```bash
cd nowledge-mem-google-antigravity
npm run validate
```

## Build The Release Artifact

```bash
cd nowledge-mem-google-antigravity
npm run package:release
```

Or run the full pre-release check:

```bash
cd nowledge-mem-google-antigravity
npm run verify:release
```

This produces:
- `dist/nowledge-mem-google-antigravity.tar.gz`
- `dist/nowledge-mem-google-antigravity.tar.gz.sha256`

The archive is intentionally flat at the root so Antigravity can inspect it as an installable plugin package.

## CI Verification

Pull requests and relevant pushes run the validation workflow. That workflow validates the manifest and also rebuilds the release archive so packaging drift is caught before tagging.

## Tagging Convention

The GitHub Actions workflow watches tags in this form:

```text
nowledge-mem-google-antigravity-v*
```

Example:

```bash
git tag nowledge-mem-google-antigravity-v0.1.0
git push origin nowledge-mem-google-antigravity-v0.1.0
```

## Initial Public Release

For the first public release, use:
- tag: `nowledge-mem-google-antigravity-v0.1.0`
- release title: `Nowledge Mem Google Antigravity v0.1.0`
- release notes source: `release-notes/0.1.0.md`
- workflow behavior: the release workflow verifies that the pushed tag matches `package.json` and publishes the matching `release-notes/<version>.md` file as the GitHub Release body

## Installation After Release

Once the tagged GitHub Release exists, users can download the release archive and install it locally:
- Extract it to `<workspace-root>/.agents/plugins/nowledge-mem-google-antigravity/`
- Or globally to `~/.gemini/config/plugins/nowledge-mem-google-antigravity/`

## Release Checklist

- bump `version` in `package.json` and `plugin.json`
- update `CHANGELOG.md`
- add `release-notes/<version>.md`
- run `npm run verify:release`
- confirm the archive root contains `plugin.json`, `assets/`, `mcp_config.json`, `hooks.json`, `package.json`, `rules/`, `skills/`, and `hooks/`
- create and push a matching tag
- publish the GitHub Release with the generated `.tar.gz` asset and checksum
- verify the repo still has the `google-antigravity-plugin` topic
- verify the archive contents with `tar -tzf dist/nowledge-mem-google-antigravity.tar.gz` and make sure there are no `._*` entries
