#!/bin/bash
# Starts KoboldCpp with the settings of the launch file /run/lyrveil/llm-launch.env (docs/configuration.md, Launch
# files, and docs/operations.md, Runtime images).
#
# The file is read as data and never sourced. Blank lines and lines starting with "#" are skipped; every other line
# must be KEY=VALUE. Each of the eleven keys must appear exactly once with an allowed value, no other key may appear,
# and /models/llm/<MODEL_FILE> must be a readable file. Any other case prints the reason, naming the key and line but
# never the value, and exits 78, which the orchestrator reports as RUNTIME_CONFIG_INVALID. On success the script
# replaces itself with the launcher, so the launcher's exit status is the container's.
#
# With the single argument --print-command it runs the same checks, then prints the launcher command, one argument per
# line, and exits 0 instead of starting it, so the checks and the command can be tested with no GPU and no model. Any
# other argument exits 64.
set -euo pipefail

readonly LAUNCH_FILE=/run/lyrveil/llm-launch.env
readonly MODEL_DIR=/models/llm
readonly LAUNCHER=/opt/koboldcpp/koboldcpp-launcher
readonly EX_USAGE=64
readonly EX_CONFIG=78

# The allowed values of docs/configuration.md as anchored extended regular expressions. MODEL_PROFILE follows the
# catalog identifier rule of lyrveil.tools.models, which custom-<12 hex> also satisfies.
declare -A ALLOWED=(
    [MODEL_PROFILE]='^[a-z0-9][a-z0-9.-]{1,47}$'
    [MODEL_FILE]='^[A-Za-z0-9._-]{1,128}\.gguf$'
    [CONTEXT_SIZE]='^(4096|6144|8192|12288|16384|24576)$'
    [GPU_LAYERS]='^(-1|[0-9]|[1-9][0-9])$'
    [BATCH_SIZE]='^(128|256|512|1024|2048)$'
    [MOE_CPU_LAYERS]='^([0-9]|[1-9][0-9])$'
    [USE_MMAP]='^[01]$'
    [KV_QUANT]='^(f16|q8_0)$'
    [SWA_PADDING]='^(0|1024|2048)$'
    [SMART_CACHE]='^(0|2)$'
    [LEVEL]='^(A|B|C|CUSTOM)$'
)
declare -A SETTING=()

fail() {
    echo "lyrveil-llm: $*" >&2
    exit "${EX_CONFIG}"
}

print_command=0
if [ "$#" -eq 1 ] && [ "$1" = "--print-command" ]; then
    print_command=1
elif [ "$#" -ne 0 ]; then
    echo "lyrveil-llm: usage: lyrveil-llm-entrypoint [--print-command]" >&2
    exit "${EX_USAGE}"
fi

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

model_path="${MODEL_DIR}/${SETTING[MODEL_FILE]}"
[ -f "${model_path}" ] && [ -r "${model_path}" ] || fail "the model file named by MODEL_FILE is missing from ${MODEL_DIR}"

# The optional flags of docs/runtime-contracts.md, each added only when its key asks for it.
optional_args=()
if [ "${SETTING[MOE_CPU_LAYERS]}" -gt 0 ]; then
    optional_args=(--moecpu "${SETTING[MOE_CPU_LAYERS]}")
fi
if [ "${SETTING[USE_MMAP]}" = "1" ]; then
    optional_args+=(--usemmap)
fi
if [ "${SETTING[SWA_PADDING]}" -gt 0 ]; then
    optional_args+=(--swapadding "${SETTING[SWA_PADDING]}")
fi
if [ "${SETTING[SMART_CACHE]}" -gt 0 ]; then
    optional_args+=(--smartcache "${SETTING[SMART_CACHE]}")
fi

# --quiet keeps prompts and replies out of the logs and is required for privacy (docs/security.md).
command=(
    "${LAUNCHER}"
    --model "${model_path}"
    --usecuda
    --contextsize "${SETTING[CONTEXT_SIZE]}"
    --gpulayers "${SETTING[GPU_LAYERS]}"
    --batchsize "${SETTING[BATCH_SIZE]}"
    --quantkv "${SETTING[KV_QUANT]}"
    "${optional_args[@]}"
    --host 0.0.0.0
    --port 5001
    --skiplauncher
    --quiet
)

if [ "${print_command}" -eq 1 ]; then
    printf '%s\n' "${command[@]}"
    exit 0
fi
exec "${command[@]}"
