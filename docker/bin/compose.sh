#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
docker_dir=$(CDPATH= cd -- "$script_dir/.." && pwd -P)

export PPTMASTER_HOST_PROJECTS_ROOT="${PPTMASTER_HOST_PROJECTS_ROOT:-$docker_dir/data/projects}"
export PPTMASTER_HOST_OPENCODE_CONFIG_ROOT="${PPTMASTER_HOST_OPENCODE_CONFIG_ROOT:-$docker_dir/opencode}"

exec docker compose --env-file "$docker_dir/.env" -f "$docker_dir/compose.yml" "$@"
