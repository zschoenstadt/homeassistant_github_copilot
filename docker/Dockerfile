# ──────────────────────────────────────────────────────────────
# Home Assistant + glibc compatibility for GitHub Copilot SDK
#
# The Copilot SDK ships a native CLI binary linked against glibc.
# The official HA image is Alpine (musl), so we install gcompat
# and supplementary Alpine packages to provide the needed symbols.
# ──────────────────────────────────────────────────────────────

ARG HA_VERSION=stable

FROM homeassistant/home-assistant:${HA_VERSION}

ARG SDK_VERSION=0.2.2

# Install glibc compatibility layer and supplementary libraries:
#   gcompat     — glibc ABI shim (provides ld-linux loader + core symbols)
#   libstdc++   — C++ standard library
#   libucontext — makecontext(), getcontext(), setcontext()
RUN apk add --no-cache \
    gcompat \
    libstdc++ \
    libucontext

# Build a small shim for symbols gcompat doesn't provide.
# On musl, fcntl is already 64-bit, so fcntl64 is just an alias.
# gnu_get_libc_version returns a dummy string — the binary only
# uses it for version logging, not for behavioral branching.
RUN apk add --no-cache --virtual .build-deps gcc musl-dev \
    && printf '%s\n' \
        '#include <fcntl.h>' \
        '#include <stdarg.h>' \
        'int fcntl64(int fd, int cmd, ...) {' \
        '    va_list ap;' \
        '    va_start(ap, cmd);' \
        '    void *arg = va_arg(ap, void *);' \
        '    va_end(ap);' \
        '    return fcntl(fd, cmd, arg);' \
        '}' \
        'const char *gnu_get_libc_version(void) {' \
        '    return "2.36";' \
        '}' \
        > /tmp/glibc_shim.c \
    && gcc -shared -fPIC -o /usr/local/lib/glibc_shim.so /tmp/glibc_shim.c \
    && rm /tmp/glibc_shim.c \
    && apk del .build-deps

# Ensure the glibc dynamic linker path exists for ELF binaries
# that hardcode /lib64/ld-linux-x86-64.so.2
RUN if [ ! -e /lib64/ld-linux-x86-64.so.2 ]; then \
        mkdir -p /lib64 && \
        ln -s /lib/ld-linux-x86-64.so.2 /lib64/ld-linux-x86-64.so.2; \
    fi

# Install the Copilot SDK with bundled CLI binary.
# The manylinux wheel bundles the native binary. Alpine's pip rejects
# manylinux platform tags, so we download then extract directly.
RUN pip download --no-cache-dir \
        --no-deps \
        --platform manylinux_2_28_x86_64 \
        --only-binary :all: \
        --dest /tmp/sdk-wheel \
        github-copilot-sdk==${SDK_VERSION} \
    && python3 -m zipfile -e \
        "$(ls /tmp/sdk-wheel/github_copilot_sdk*.whl)" \
        "$(python3 -c 'import site; print(site.getsitepackages()[0])')" \
    && rm -rf /tmp/sdk-wheel

# Wrap the CLI binary so LD_PRELOAD only applies to it, not the
# entire container. Move the real binary aside and replace it with
# a script that sets LD_PRELOAD before exec-ing the real one.
RUN SITE="$(python3 -c 'import site; print(site.getsitepackages()[0])')" \
    && mv "$SITE/copilot/bin/copilot" "$SITE/copilot/bin/copilot.real" \
    && printf '#!/bin/sh\nexec env LD_PRELOAD="/usr/local/lib/glibc_shim.so" "%s/copilot/bin/copilot.real" "$@"\n' "$SITE" \
        > "$SITE/copilot/bin/copilot" \
    && chmod +x "$SITE/copilot/bin/copilot" "$SITE/copilot/bin/copilot.real"
