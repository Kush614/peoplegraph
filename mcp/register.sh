#!/usr/bin/env bash
# Register the official Neo4j Cypher MCP server with Claude Code / Claude Desktop / Qoder
# using the credentials in ../.env.  Usage:  mcp/register.sh [claude-code|print]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
: "${NEO4J_URI:?set NEO4J_URI in .env}"; : "${NEO4J_PASSWORD:?set NEO4J_PASSWORD in .env}"
USER_="${NEO4J_USER:-neo4j}"; DB="${NEO4J_DATABASE:-neo4j}"
case "${1:-print}" in
  claude-code)
    claude mcp add neo4j-peoplegraph -s user \
      -e NEO4J_URI="$NEO4J_URI" -e NEO4J_USERNAME="$USER_" -e NEO4J_PASSWORD="$NEO4J_PASSWORD" -e NEO4J_DATABASE="$DB" \
      -- uvx -p 3.12 mcp-neo4j-cypher@0.6.0 --transport stdio
    echo "registered. Restart Claude Code, then ask: 'Who is my warmest path to anyone at Stripe?'" ;;
  *)
    sed -e "s#neo4j+s://xxxxxxxx.databases.neo4j.io#$NEO4J_URI#" -e "s#<password>#$NEO4J_PASSWORD#" \
        -e "s#\"neo4j\",#\"$USER_\",#" mcp/claude_desktop_config.example.json
    echo; echo "^ paste into ~/Library/Application Support/Claude/claude_desktop_config.json (or Qoder's MCP settings)" ;;
esac
