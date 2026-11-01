FROM archlinux:base

ARG BUILD_DATE
RUN test -n "$BUILD_DATE" \
    && printf 'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch\n' > /etc/pacman.d/mirrorlist \
    && pacman -Syu --needed --noconfirm \
       base-devel devtools nvchecker namcap shellcheck actionlint git python \
    && useradd --create-home builder

ENV LANG=C.UTF-8
ENV TERM=xterm
WORKDIR /repo
