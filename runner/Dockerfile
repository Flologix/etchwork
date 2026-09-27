# The per-job runner (board-service spec §4.1): the engine image plus flai.
ARG ENGINE_IMAGE
FROM ${ENGINE_IMAGE}
COPY flai /usr/local/bin/flai
COPY etchwork-job /usr/local/bin/etchwork-job
COPY probes.sh /usr/local/bin/etchwork-probes
COPY SKILL.md flai-addendum.md /opt/etchwork/skill/
RUN chmod 0755 /usr/local/bin/flai /usr/local/bin/etchwork-job /usr/local/bin/etchwork-probes
ENTRYPOINT ["/usr/local/bin/etchwork-job"]
