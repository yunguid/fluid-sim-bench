# Sandbox for building and running model-written programs.
#
# Jobs run with no network, a read-only root, all capabilities dropped, and
# write access only to their own job folder. See bench/RULES.md.
FROM archlinux:latest
RUN pacman -Syu --noconfirm --needed \
        base-devel libx11 libxi libxcursor mesa \
        xorg-server-xvfb xdotool ffmpeg python \
    && pacman -Scc --noconfirm
# Make the host user's uid/gid usable inside, so outputs are owned by them.
RUN groupadd -g 1000 bench && useradd -u 1000 -g 1000 -M -d /tmp bench
ENV HOME=/tmp
