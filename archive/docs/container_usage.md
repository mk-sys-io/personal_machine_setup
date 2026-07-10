# Container usage

## Why Alpine for testing

- **Minimal image** (~5 MB), pulls in seconds
- **Single stable CDN** (`dl-cdn.alpinelinux.org`) — no implicit mirror discovery
- **No GPG keyring refresh**, no multi-mirror fallback logic to break behind allowlist
- Other distros (Debian, Ubuntu, Fedora, Arch) have multiple mirror domains, security key servers, and update channels not in the allowlist → package installs fail when locked
- **Exception**: language-specific images built on Alpine (`python:3-alpine`, `node:alpine`, `golang:alpine`) work fine — they share the same CDN

## Podman daily commands

### Run containers

| Goal | Command |
|---|---|
| Run and auto-delete | `podman run --rm alpine sh -c "apk add curl && curl -s example.com"` |
| Interactive shell | `podman run -it alpine sh` |
| Background (detached) | `podman run -d --name dev alpine sleep infinity` |
| Name a container | `podman run --name myapp alpine` |
| Mount host directory | `podman run -v /home/mike/project:/workspace alpine` |
| Set environment variable | `podman run -e KEY=value alpine` |
| Port mapping | `podman run -p 8080:80 nginx` |

### Manage containers

| Goal | Command |
|---|---|
| List running | `podman ps` |
| List all | `podman ps -a` |
| Exec into running | `podman exec -it dev sh` |
| View logs | `podman logs dev` |
| Stop | `podman stop dev` |
| Start | `podman start dev` |
| Remove | `podman rm dev` |
| Force remove | `podman rm -f dev` |
| Copy file into container | `podman cp file.txt dev:/dest/` |
| Copy file from container | `podman cp dev:/src/output.tar ./` |
| Inspect details | `podman inspect dev` |

### Manage images

| Goal | Command |
|---|---|
| List cached images | `podman images` |
| Pull image | `podman pull alpine` |
| Remove image | `podman image rm alpine` |
| Remove unused images | `podman image prune` |

## Temporary vs persistent

| Pattern | Use case | Cleanup |
|---|---|---|
| `--rm` | One-off test, build, or verify | Automatic on exit |
| No `--rm` + `--name` | Dev server, long-running service | Manual `podman stop` + `podman rm` |
| `-d` (detach) | Background task | Manual stop/rm |
| `-it` (interactive) | Shell exploration, debugging | `exit` or Ctrl-D |

## Examples

### Mount source code and run a build

```bash
podman run --rm -v /home/mike/project:/workspace alpine sh -c "
  cd /workspace
  apk add build-base
  make
"
```

### Persistent dev environment

```bash
podman run -d --name dev -v /home/mike/project:/workspace alpine sleep infinity
podman exec -it dev sh
# ... do work inside container ...
podman stop dev
podman start dev   # resume later
podman rm -f dev   # tear down
```

### Port mapping with environment variables

```bash
podman run --rm -p 8080:80 -e APP_ENV=production nginx:alpine
```

### Copy build artifacts out

```bash
podman run --rm alpine sh -c "
  apk add build-base
  echo 'hello' > /output.txt
" -v /home/mike/output:/out
podman cp <container-id>:/out/output.txt ./
```
