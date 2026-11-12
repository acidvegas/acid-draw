#!/bin/sh
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# setup.sh

set -e
docker build -t aciddraw .
docker rm -f aciddraw 2>/dev/null || true
ARCADE_ARGS=''
if [ -n "$ARCADE_URL" ]; then
	ARCADE_PUBLIC_URL=${ARCADE_PUBLIC_URL:-'https://arcade.{domain}'} # {domain} becomes the player's parent domain
	ARCADE_ARGS="--network arcade -e ARCADE_URL -e ARCADE_PUBLIC_URL=$ARCADE_PUBLIC_URL -e ARCADE_GAME=draw"
fi
docker run -d \
	--name aciddraw \
	--restart unless-stopped \
	-p ${DRAW_BIND:-127.0.0.1}:20010:20010 \
	-e DRAW_PROXIES=${DRAW_PROXIES:-0} \
	-e DRAW_ADMINS=${DRAW_ADMINS:-} \
	${DRAW_PUBLIC_URL:+-e DRAW_PUBLIC_URL} \
	-v aciddraw_data:/data \
	$ARCADE_ARGS \
	aciddraw
