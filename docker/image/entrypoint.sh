#!/bin/bash
# Starts ComfyUI with the settings of the launch file /run/lyrveil/image-launch.env (docs/configuration.md, Launch
# files, and docs/operations.md, Runtime images).
#
# The file is read as data and never sourced. Blank lines and lines starting with "#" are skipped; every other line
# must be KEY=VALUE. RESERVE_VRAM_GB must appear exactly once with an allowed value and no other key may appear. Any
# other case prints the reason, naming the key and line but never the value, and exits 78, which the orchestrator
# reports as RUNTIME_CONFIG_INVALID. On success the script creates the folders ComfyUI writes to in the tmpfs
# /tmp/comfy, then replaces itself with ComfyUI, so ComfyUI's exit status is the container's.
#
# Arguments, each at most once and in any order:
#   --cpu            adds ComfyUI's --cpu, so the runtime starts with no GPU. For tests only; production never passes it.
#   --print-command  runs the same checks, prints the command, one argument per line, and exits 0 instead of starting it.
# Any other argument exits 64.
set -euo pipefail

readonly LAUNCH_FILE=/run/lyrveil/image-launch.env
readonly COMFY_ROOT=/opt/ComfyUI
readonly STATE_DIR=/tmp/comfy
readonly EX_USAGE=64
readonly EX_CONFIG=78

# The allowed values of docs/configuration.md as anchored extended regular expressions: 0 to 4 in steps of 0.5.
declare -A ALLOWED=(
    [RESERVE_VRAM_GB]='^([0-3](\.[05])?|4(\.0)?)$'
)
declare -A SETTING=()

fail() {
    echo "lyrveil-image: $*" >&2
    exit "${EX_CONFIG}"
}

usage() {
    echo "lyrveil-image: usage: lyrveil-image-entrypoint [--cpu] [--print-command]" >&2
    exit "${EX_USAGE}"
}

cpu=0
print_command=0
for argument in "$@"; do
    case "${argument}" in
        --cpu) [ "${cpu}" -eq 0 ] || usage; cpu=1 ;;
        --print-command) [ "${print_command}" -eq 0 ] || usage; print_command=1 ;;
        *) usage ;;
    esac
done

[ -f "${LAUNCH_FILE}" ] || fail "the launch file ${LAUNCH_FILE} is missing"
[ -r "${LAUNCH_FILE}" ] || fail "the launch file ${LAUNCH_FILE} is not readable"

line_number=0
# "|| [ -n ... ]" keeps a last line that has no final newline.
while IFS= read -r line || [ -n "${line}" ]; do
    line_number=$((line_number + 1))
    if [ -z "${line}" ] || [ "${line:0:1}" = "#" ]; then
        continue
    fi
    if [[ ! "${line}" =~ ^([A-Z_]+)=(.*)$ ]]; then
        fail "line ${line_number} of the launch file is not KEY=VALUE"
    fi
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    [ -n "${ALLOWED[${key}]+set}" ] || fail "line ${line_number} of the launch file has the unknown key ${key}"
    [ -z "${SETTING[${key}]+set}" ] || fail "line ${line_number} of the launch file repeats the key ${key}"
    [[ "${value}" =~ ${ALLOWED[${key}]} ]] || fail "line ${line_number} of the launch file has an invalid value for ${key}"
    SETTING[${key}]="${value}"
done < "${LAUNCH_FILE}"

for key in "${!ALLOWED[@]}"; do
    [ -n "${SETTING[${key}]+set}" ] || fail "the launch file has no ${key}"
done

# The flags of docs/runtime-contracts.md. Every directory ComfyUI writes to is in the tmpfs, so it keeps no state.
command=(
    python "${COMFY_ROOT}/main.py"
    --listen 0.0.0.0 --port 8188
    --disable-auto-launch --offline --disable-all-custom-nodes --disable-metadata
    --preview-method none --log-stdout
    --output-directory "${STATE_DIR}/output" --input-directory "${STATE_DIR}/input"
    --temp-directory "${STATE_DIR}/temp" --user-directory "${STATE_DIR}/user"
    --reserve-vram "${SETTING[RESERVE_VRAM_GB]}"
)
if [ "${cpu}" -eq 1 ]; then
    command+=(--cpu)
fi

if [ "${print_command}" -eq 1 ]; then
    printf '%s\n' "${command[@]}"
    exit 0
fi
# HOME and TMPDIR of the image point here. The tmpfs starts empty at every start, so nothing survives a restart.
mkdir -p "${STATE_DIR}/output" "${STATE_DIR}/input" "${STATE_DIR}/temp" "${STATE_DIR}/user" "${STATE_DIR}/home" "${STATE_DIR}/tmp"
cd "${COMFY_ROOT}"
exec "${command[@]}"
