FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG RUST_VERSION=1.96.0
ARG BUN_VERSION=1.3.3

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        clang \
        cmake \
        curl \
        dpkg-dev \
        file \
        git \
        glslang-tools \
        glslc \
        jq \
        libasound2-dev \
        libayatana-appindicator3-dev \
        libclang-dev \
        libevdev-dev \
        libgtk-3-dev \
        libgtk-layer-shell-dev \
        librsvg2-dev \
        libssl-dev \
        libudev-dev \
        libvulkan-dev \
        libwebkit2gtk-4.1-dev \
        libx11-dev \
        libxtst-dev \
        patchelf \
        pkg-config \
        spirv-headers \
        unzip \
        vulkan-tools \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain "${RUST_VERSION}" \
    && /root/.cargo/bin/rustup component add rustfmt clippy \
    && cp -a /root/.cargo /usr/local/cargo \
    && cp -a /root/.rustup /usr/local/rustup

RUN curl --proto '=https' --tlsv1.2 -fsSL \
        "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-x64.zip" \
        -o /tmp/bun.zip \
    && unzip -q /tmp/bun.zip -d /tmp/bun \
    && install -m 0755 /tmp/bun/bun-linux-x64/bun /usr/local/bin/bun \
    && rm -rf /tmp/bun /tmp/bun.zip

RUN mkdir -p /work /tmp/qq-builder \
    && chown -R 1000:1000 /work /tmp/qq-builder \
    && chmod -R a+rX /usr/local/cargo /usr/local/rustup

ENV PATH="/usr/local/cargo/bin:/usr/local/bin:${PATH}"
ENV RUSTUP_HOME="/usr/local/rustup"
ENV HOME="/tmp/qq-builder"
WORKDIR /work
