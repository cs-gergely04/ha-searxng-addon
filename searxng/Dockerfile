ARG BUILD_VERSION
ARG BUILD_ARCH

FROM docker.io/searxng/searxng:2026.5.2-cd75013c9

LABEL \
  io.hass.name="SearXNG" \
  io.hass.description="Private SearXNG metasearch engine with persistent settings and built-in MCP server" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  io.hass.arch="${BUILD_ARCH}"

COPY run.sh /run.sh
COPY mcp_server.py /mcp_server.py
RUN chmod a+x /run.sh /mcp_server.py

# Override the base image entrypoint so our script runs first
ENTRYPOINT ["/run.sh"]
