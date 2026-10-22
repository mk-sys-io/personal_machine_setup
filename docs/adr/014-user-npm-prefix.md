# npm globals install to the user prefix, never sudo

- Status: Accepted (2026-10-22)

## Decision

`lib/20-packages.py` converges `npm config set prefix ~/.local` before
the npm package loop, installs with plain `npm install -g` (no sudo),
and uses `npm ls -g --depth=0 <spec>` as the idempotency oracle.
`ensure_npm_prefix()` fails closed when the effective prefix is not
`$HOME/.local` (write failure or `NPM_CONFIG_PREFIX` override) rather
than falling back to a privileged install.

## Why

Node from NodeSource lives at `/usr/bin/node`, so npm's default global
prefix is `/usr`, which is root-owned; `sudo npm install -g` also ran
registry lifecycle scripts as root and left root-owned trees that broke
later user-level updates. npm's own guidance is a user-owned prefix or a
version manager, not sudo.
`Rejected: status-quo sudo — root postinstall, root-owned files;
nvm/fnm — a new toolchain layer outside package convergence;
per-command --prefix env — fixes the installer only, not manual
npm install -g.`
`Revisit only if: a package genuinely requires a system-wide install.`

## Consequences

- Good: user-owned tree under `~/.local`, manual `npm install -g` works,
  no root lifecycle scripts.
- Bad (accepted cost): `~/.npmrc` is installer-converged rather than
  dotfile-deployed; user-level postinstall still runs with the user's
  files and keys — blast radius is reduced, not eliminated.
