#!/usr/bin/env bash
# CLI for managing frames JSON files (CRUD)
#
# Frame JSON Schema:
# {
#   "id": "unique_frame_id",
#   "p": "prompt_or_task_description",
#   "parent_id": "id_of_parent_frame_or_null",
#   "children": ["array_of_child_frame_ids"],
#   "depth": integer_depth_level,
#   "status": "init|in_progress|completed|failed",
#   "knowledge": "any_knowledge_to_pass_down_to_children",
#   "result": "final_result_or_observation"
# }
#
# Usage:
#  ./frames.sh <command> [args]
#
# Commands:
#  create <p> [parent_id] [inherited_knowledge] - Create a new frame
#  read <frame_id> [field] - Read a frame's JSON content or a specific field
#  result <frame_id> [result] - Update a frame's result and mark it as completed
#                               If result is omitted, read it from stdin
#  update <frame_id> <field> <value> - Update a specific field in a frame's JSON
#
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
FRAMES_DIR="$SCRIPT_DIR/frames"
mkdir -p "$FRAMES_DIR"

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./frames.sh <command> [args]

Commands:
  create <p> [parent_id] [inherited_knowledge]
  read <frame_id> [field]
  result <frame_id> [result]
  update <frame_id> <field> <value>
EOF
}

require_tools() {
    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq is required but not installed." >&2
        exit 1
    fi
}

frame_path() {
    local frame_id="$1"
    echo "$FRAMES_DIR/frame_${frame_id}.json"
}

frame_exists() {
    local frame_id="$1"
    [[ -f "$(frame_path "$frame_id")" ]]
}

read_field() {
    local frame_file="$1"
    local field="$2"
    jq "has(\"${field}\")" "$frame_file" | grep -q true
}

generate_frame_id() {
    local timestamp=$(date "+%d%H%M%S")
    local rand_suffix=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 4 | head -n 1)
    echo "${timestamp}_${rand_suffix}"
}

create_frame() {
    local prompt="$1"
    local parent_id="${2:-}"
    local inherited_knowledge="${3:-}"
    local frame_id
    local frame_file
    local parent_depth=0
    local depth=0

    frame_id="$(generate_frame_id)"
    frame_file="$(frame_path "$frame_id")"

    if [[ -n "$parent_id" ]]; then
        if ! frame_exists "$parent_id"; then
            echo "Error: parent frame '$parent_id' does not exist." >&2
            exit 1
        fi
        parent_depth="$(jq -r '.depth // 0' "$(frame_path "$parent_id")")"
        depth=$((parent_depth + 1))

        if [[ -z "$inherited_knowledge" ]]; then
            inherited_knowledge="$(jq -r '.knowledge // ""' "$(frame_path "$parent_id")")"
        fi
    fi

    jq -n \
        --arg id "$frame_id" \
        --arg p "$prompt" \
        --arg parent_id "$parent_id" \
        --argjson depth "$depth" \
        --arg knowledge "$inherited_knowledge" \
        '{
            id: $id,
            p: $p,
            parent_id: (if $parent_id == "" then null else $parent_id end),
            children: [],
            depth: $depth,
            status: "init",
            knowledge: $knowledge,
            result: ""
        }' > "$frame_file"

    if [[ -n "$parent_id" ]]; then
        jq --arg child_id "$frame_id" '
            .children = ((.children // []) + [$child_id] | unique)
        ' "$(frame_path "$parent_id")" > "$(frame_path "$parent_id").tmp"
        mv "$(frame_path "$parent_id").tmp" "$(frame_path "$parent_id")"
    fi

    echo "$frame_id"
}

read_frame() {
    local frame_id="$1"
    local field="${2:-}"
    local frame_file

    if ! frame_exists "$frame_id"; then
        echo "Error: frame '$frame_id' does not exist." >&2
        exit 1
    fi

    frame_file="$(frame_path "$frame_id")"

    if [[ -z "$field" ]]; then
        jq '.' "$frame_file"
        return
    fi

    if ! read_field "$frame_file" "$field"; then
        echo "Error: field '$field' not found in frame '$frame_id'." >&2
        exit 1
    fi

    jq -r ".${field} // empty" "$frame_file"
}

update_result() {
    local frame_id="$1"
    local result="$2"
    local frame_file

    if ! frame_exists "$frame_id"; then
        echo "Error: frame '$frame_id' does not exist." >&2
        exit 1
    fi

    frame_file="$(frame_path "$frame_id")"
    jq --arg result "$result" '.result = $result | .status = "completed"' "$frame_file" > "${frame_file}.tmp"
    mv "${frame_file}.tmp" "$frame_file"

    jq '.' "$frame_file"
}

update_result_from_stdin() {
    local frame_id="$1"
    local frame_file

    if ! frame_exists "$frame_id"; then
        echo "Error: frame '$frame_id' does not exist." >&2
        exit 1
    fi

    frame_file="$(frame_path "$frame_id")"
    jq --rawfile result /dev/stdin '.result = $result | .status = "completed"' "$frame_file" > "${frame_file}.tmp"
    mv "${frame_file}.tmp" "$frame_file"

    jq '.' "$frame_file"
}

update_frame_field() {
    local frame_id="$1"
    local field="$2"
    local value="$3"
    local frame_file

    if ! frame_exists "$frame_id"; then
        echo "Error: frame '$frame_id' does not exist." >&2
        exit 1
    fi

    frame_file="$(frame_path "$frame_id")"

    if ! read_field "$frame_file" "$field"; then
        echo "Error: field '$field' not found in frame '$frame_id'." >&2
        exit 1
    fi

    # Preserve JSON type where possible (number, bool, object, array), fallback to string.
    if jq -e --argjson value "$value" ".${field} = \$value" "$frame_file" >/dev/null 2>&1; then
        jq --argjson value "$value" ".${field} = \$value" "$frame_file" > "${frame_file}.tmp"
    else
        jq --arg value "$value" ".${field} = \$value" "$frame_file" > "${frame_file}.tmp"
    fi

    mv "${frame_file}.tmp" "$frame_file"
    jq '.' "$frame_file"
}

main() {
    require_tools
    mkdir -p "$FRAMES_DIR"

    local cmd="${1:-}"

    case "$cmd" in
        create)
            if [[ $# -lt 2 ]]; then
                usage
                exit 1
            fi
            create_frame "$2" "${3:-}" "${4:-}"
            ;;
        read)
            if [[ $# -lt 2 ]]; then
                usage
                exit 1
            fi
            read_frame "$2" "${3:-}"
            ;;
        result)
            if [[ $# -lt 2 ]]; then
                usage
                exit 1
            fi
            if [[ $# -ge 3 ]]; then
                update_result "$2" "$3"
            else
                if [[ -t 0 ]]; then
                    echo "Error: missing result. Provide it as an argument or pipe it through stdin." >&2
                    exit 1
                fi
                update_result_from_stdin "$2"
            fi
            ;;
        update)
            if [[ $# -lt 4 ]]; then
                usage
                exit 1
            fi
            update_frame_field "$2" "$3" "$4"
            ;;
        ""|-h|--help|help)
            usage
            ;;
        *)
            echo "Error: unknown command '$cmd'." >&2
            usage
            exit 1
            ;;
    esac
}

main "$@"
