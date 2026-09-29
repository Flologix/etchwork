# etchwork-engine: the deterministic PCB toolchain for Etchwork's /etchwork tools.
# KiCad 10 (kicad-cli, pcbnew), kinet2pcb, freerouting, SKiDL, and the
# circuit_mcp package, served over MCP stdio. Base recipe from the old
# harness's deploy/kicad.Dockerfile (see its comments for why each piece).
ARG KICAD_TAG=10.0
FROM kicad/kicad:${KICAD_TAG}

USER root
ARG FREEROUTING_VERSION=2.4.1
ARG FREEROUTING_SHA256=251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9

# openjdk-25: freerouting 2.4.1 is class-file 69; Debian 13's default JRE (21) cannot load it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip openjdk-25-jre-headless curl \
    && rm -rf /var/lib/apt/lists/*

# The system python3 is the only one with pcbnew, so packages go there.
# Remove the system typing-extensions that has no RECORD file to avoid uninstall issues
#
# kinet2pcb is pinned (unlike an unpinned "latest" would be) because the sed
# patch just below targets one exact line of ITS source; an unpinned install
# could silently pick up a release that moved or fixed that line, and the
# patch step is written to fail loudly rather than silently no-op in that
# case -- see the comment there.
RUN rm -rf /usr/lib/python3/dist-packages/typing_extensions* && \
    pip install --no-cache-dir --break-system-packages \
        kinet2pcb==1.1.4 skidl==2.3.0 mcp==1.30.0 pytest

# kinet2pcb 1.1.4's global fp-lib-table loader compares the TYPE field without
# stripping its quotes -- `type_.lower() != "kicad"` against `'"kicad"'` -- so
# every KiCad-type library entry is silently skipped and build_board's
# kinet2pcb call fails on the first footprint with KeyError, regardless of
# which library it names. The package ships its own `rmv_quotes` helper (used
# on the URI and nickname two lines below) but never calls it here. Measured
# 2026-09-23 against 1.1.4, the current release: with the patch, the global
# table's 155 libraries load; without it, none do. One-line fix at the
# upstream bug rather than a workaround, so build_board's ported fp_libs
# behaviour is unchanged.
#
# `grep -q ... &&` first: if a future rebuild ever installs a kinet2pcb whose
# source no longer has this exact line -- the pin above should prevent that,
# but a pin is only as good as the rebuild that respects it -- the grep fails,
# `&&` short-circuits the sed, and the RUN step (and so the build) fails
# loudly instead of silently shipping the unpatched, always-empty library
# table again.
RUN grep -q 'if type_\.lower() != "kicad":' \
        /usr/local/lib/python3.13/dist-packages/kinet2pcb/kinet2pcb.py && \
    sed -i 's/if type_\.lower() != "kicad":/if rmv_quotes(type_).lower() != "kicad":/' \
        /usr/local/lib/python3.13/dist-packages/kinet2pcb/kinet2pcb.py

RUN curl -fsSL -o /opt/freerouting.jar \
    "https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar" \
 && echo "${FREEROUTING_SHA256}  /opt/freerouting.jar" | sha256sum -c -

ENV FREEROUTING_JAR=/opt/freerouting.jar \
    KICAD_SYMBOL_DIR=/usr/share/kicad/symbols \
    KICAD6_SYMBOL_DIR=/usr/share/kicad/symbols \
    KICAD7_SYMBOL_DIR=/usr/share/kicad/symbols \
    KICAD8_SYMBOL_DIR=/usr/share/kicad/symbols \
    KICAD9_SYMBOL_DIR=/usr/share/kicad/symbols \
    KICAD10_SYMBOL_DIR=/usr/share/kicad/symbols \
    KICAD_FOOTPRINT_DIR=/usr/share/kicad/footprints \
    # Only KICAD10_FOOTPRINT_DIR is actually referenced by this image's own
    # fp-lib-table today (see /usr/share/kicad/template/fp-lib-table); the
    # 6..9 variants are set anyway to mirror the KICAD{6..10}_SYMBOL_DIR
    # pattern above, in case a table from an older KiCad ever gets loaded.
    KICAD6_FOOTPRINT_DIR=/usr/share/kicad/footprints \
    KICAD7_FOOTPRINT_DIR=/usr/share/kicad/footprints \
    KICAD8_FOOTPRINT_DIR=/usr/share/kicad/footprints \
    KICAD9_FOOTPRINT_DIR=/usr/share/kicad/footprints \
    KICAD10_FOOTPRINT_DIR=/usr/share/kicad/footprints \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/etchwork

COPY circuit_mcp /opt/etchwork/circuit_mcp
COPY tests /opt/etchwork/tests
WORKDIR /workspace
# -P: the working directory is the project, and `python3 -m` would otherwise
# put it first on sys.path, where a folder named `mcp` replaced the MCP library.
ENTRYPOINT ["python3", "-P", "-m", "circuit_mcp.server"]
