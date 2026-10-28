#!/bin/sh
# Keep this project's MCP request from being routed to the sandbox parent.
set -eu
template=/home/dify/course/docker/ssrf_proxy/squid.conf.template
container=docker-ssrf_proxy-1
test -f "$template"
if grep -q '^acl course_mcp ' "$template"; then
    echo 'Course MCP rule already exists; preserved without change.'
    exit 1
fi
work=$(mktemp -d /tmp/course-mcp-proxy.XXXXXX)
stamp=$(date +%Y%m%d-%H%M%S)
docker exec "$container" cat /etc/squid/squid.conf > "$work/squid.conf"
cat > "$work/rule" <<'EOF'

# Direct destination only for this course MCP endpoint, through Squid.
acl course_mcp dst 192.168.18.231/32
acl course_mcp_port port 8095
always_direct allow course_mcp course_mcp_port
EOF
cat "$work/rule" >> "$work/squid.conf"
docker cp "$work/squid.conf" "$container:/tmp/course-mcp-candidate.conf"
docker exec "$container" squid -k parse -f /tmp/course-mcp-candidate.conf
backup="$template.before-course-mcp-$stamp"
test ! -e "$backup"
cp -p "$template" "$backup"
docker exec "$container" cp -p /etc/squid/squid.conf "/etc/squid/squid.conf.before-course-mcp-$stamp"
cat "$work/rule" >> "$template"
docker cp "$work/squid.conf" "$container:/etc/squid/squid.conf"
docker exec "$container" squid -k reconfigure
printf 'TEMPLATE_BACKUP=%s\n' "$backup"
sha256sum "$backup" "$template"
