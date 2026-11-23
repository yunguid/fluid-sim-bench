# Sandbox for building and testing model-written programs.
#
# Entries compile to WebAssembly with Emscripten and are tested in headless
# Chromium with software WebGL2 (SwiftShader). Jobs run with no network, a
# read-only root, all capabilities dropped, and write access only to their
# own job folder. See bench/RULES.md.
FROM archlinux:latest
RUN pacman -Syu --noconfirm --needed \
        make emscripten chromium ttf-dejavu \
        python python-websockets python-pillow \
    && pacman -Scc --noconfirm
# Make the host user's uid/gid usable inside, so outputs are owned by them.
RUN groupadd -g 1000 bench && useradd -u 1000 -g 1000 -M -d /tmp bench
ENV HOME=/tmp \
    PATH=/usr/lib/emscripten:$PATH \
    EM_CACHE=/repo/build/emcache
