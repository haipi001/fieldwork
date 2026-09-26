#!/bin/sh
set -eu
collector_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=${1:-/tmp/fieldwork-native-ai-collector}
xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror \
  -framework Foundation -lbsm -lEndpointSecurity "$collector_dir/collector.m" -o "$output"
printf 'Built prototype: %s\n' "$output"
