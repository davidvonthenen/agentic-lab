FROM docker.io/opensearchproject/opensearch:3.5.0

USER root

#
# ---------------------------------------------------------------------------
# Preloaded OpenSearch data
# ---------------------------------------------------------------------------
#
# The source data must come from a cleanly stopped OpenSearch 3.5.0 node.
#
# This copies the complete OpenSearch node state, including:
#
#   nodes/0/_state
#   nodes/0/indices
#   Lucene segments
#   HNSW vector indexes
#   translogs
#   cluster metadata
#   index metadata
#
# Do not copy individual index directories independently.
#
RUN rm -rf /usr/share/opensearch/data \
    && mkdir -p /usr/share/opensearch/data
COPY --chown=1000:1000 \
    opensearch/data/ \
    /usr/share/opensearch/data/

#
# ---------------------------------------------------------------------------
# Snapshot repository
# ---------------------------------------------------------------------------
#
RUN mkdir -p /mnt/snapshots
COPY --chown=1000:1000 \
    opensearch/snapshots/ \
    /mnt/snapshots/

#
# Allow /mnt/snapshots to be used as an OpenSearch filesystem
# snapshot repository.
#
RUN printf '\npath.repo: ["/mnt/snapshots"]\n' \
    >> /usr/share/opensearch/config/opensearch.yml

#
# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
#
# The official OpenSearch image runs OpenSearch as UID/GID 1000.
#
RUN chown -R 1000:1000 \
    /usr/share/opensearch/data \
    /mnt/snapshots

#
# Return to the OpenSearch runtime user.
#
USER 1000