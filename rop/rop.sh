#!/usr/bin/env bash
MODEL="qwen3.5"
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
PROMPT="$1"

if [ -z "$PROMPT" ]; then
    echo "Usage: $0 <prompt>"
    exit 1
fi

# Build a context section based on the frame_id that looks like this:
# ----
# STACK: [Root Prompt] -> [...Parent Prompt...] -> [Current Prompt]
# KNOWLEDGE: Immediate parent frame's knowledge (if any); leave this line out if no knowledge
# [child prompt]
# <child result>
# ----
# NOTE: The child prompt and result sections are repeated for each immediate child frame.
build_context() {
    local frame_id="$1"
    local context=""
    local current_id="$frame_id"
    local prompt
    local parent_id
    local parent_knowledge=""
    local children_json=""
    local child_id=""
    local child_prompt=""
    local child_result=""
    local -a stack=()

    while [ -n "$current_id" ] && [ "$current_id" != "null" ]; do
        prompt="$(frames read "$current_id" p)"
        stack=("$prompt" "${stack[@]}")

        parent_id="$(frames read "$current_id" parent_id)"
        if [ "$current_id" = "$frame_id" ] && [ -n "$parent_id" ] && [ "$parent_id" != "null" ]; then
            parent_knowledge="$(frames read "$parent_id" knowledge)"
        fi

        current_id="$parent_id"
    done

    context="STACK: "
    local i
    for i in "${!stack[@]}"; do
        if [ "$i" -gt 0 ]; then
            context+=" -> "
        fi
        context+="[${stack[$i]}]"
    done

    if [ -n "$parent_knowledge" ]; then
        context+="\nKNOWLEDGE: $parent_knowledge"
    fi

    children_json="$(frames read "$frame_id" children)"
    while IFS= read -r child_id; do
        if [ -z "$child_id" ]; then
            continue
        fi
        child_prompt="$(frames read "$child_id" p)"
        child_result="$(frames read "$child_id" result)"
        context+="\n[$child_prompt]\n<$child_result>"
    done < <(jq -r '.[]?' <<< "$children_json")

    echo -e "----\n$context\n----"
}

# Build a "[ancestor_id>parent_id>current_id]" string for the given frame_id
build_frame_line_pfx() {
    local frame_id="$1"
    local line_pfx=""
    local current_id="$frame_id"
    local parent_id
    local -a ids=()

    while [ -n "$current_id" ] && [ "$current_id" != "null" ]; do
        ids=("$current_id" "${ids[@]}")
        parent_id="$(frames read "$current_id" parent_id)"
        current_id="$parent_id"
    done

    local i
    line_pfx="["
    for i in "${!ids[@]}"; do
        if [ "$i" -gt 0 ]; then
            line_pfx+=">"
        fi
        line_pfx+="${ids[$i]}"
    done
    line_pfx+="]"

    echo "$line_pfx"
}

# Exit code 0 if TRUE or 1 if FALSE for a prompt
# Usage: evaluate <prompt> [frame_id] - evaluates the prompt and returns YES or NO
evaluate() {
    local p="$1"
    local frame_id="${2:-}"
    local context=""
    if [ -n "$frame_id" ]; then
        context="\n$(build_context "$frame_id")"
    fi
    local full_prompt="INSTRUCTIONS: Output only YES or NO.\n\n${p}${context}\n\nOutput only YES or NO"
    local raw_response=$(echo -e "$full_prompt" | llm)
    local normalized_response=$(echo "$raw_response" | tr -d '\r\n' | tr '[:lower:]' '[:upper:]')
    grep -q "YES" <<< "$normalized_response"
}

extract_knowledge() {
    local p="$1"
    local frame_id="${2:-}"
    local context=""
    if [ -n "$frame_id" ]; then
        context="\n$(build_context "$frame_id")"
    fi
    local full_prompt="INSTRUCTIONS: Extract any relevant knowledge from the following and output it in a concise manner.\n\n${p}${context}\n\nExtracted knowledge:"
    echo -e "$full_prompt" | llm
}

# A simple wrapper to call the frames.sh script (for managing frame JSON files)
# Usage: frames <command> [args]
frames() {
    "$SCRIPT_DIR/frames.sh" "$@"
}

# A simple wrapper to call the llm.sh script (for interacting with the LLM)
# Usage: llm <prompt> - sends the prompt to the LLM and returns the output
llm() {
    # echo "$(cat)" >&2
    # echo "YES"
    "$SCRIPT_DIR/llm.sh" "$@"
}

# Evaluate if the task requires planning by checking the prompt with the LLM and return YES or NO
# Usage: require_plan <frame_id> - checks the prompt in the specified frame
require_plan() {
    local frame_id="${1:-}"
    local p="$(frames read "$frame_id" p)"
    evaluate "Does this task require planning: $p" $frame_id
}

plan() {
    local frame_id="${1:-}"
    local context="$(build_context "$frame_id")"
    local full_prompt="INSTRUCTIONS: Plan to do the following with one discrete task per line.\n\n${p}${context}\n\nPlan with one discrete task per line."
    echo -e "$full_prompt" | llm
}

run() {
    local frame_id="${1:-}"
    local parent_id="$(frames read "$frame_id" parent_id)"
    local p="$(frames read "$frame_id" p)"

    # fail if frame_id is not provided
    if [ -z "$frame_id" ]; then
        echo "Error: frame_id is required to run a frame." >&2
        exit 1
    fi

    # check if planning is required
    if require_plan "$frame_id"; then
        echo "$(build_frame_line_pfx "$frame_id") PLAN: ${p}" >&2
        local task
        local child_id
        while IFS= read -r task; do
            if [ -z "${task//[[:space:]]/}" ]; then
                continue
            fi

            # Create child frame
            child_id="$(frames create "$task" "$frame_id")"

            # Run child frame
            run "$child_id"

        done < <(plan "$frame_id")
    fi

    echo "$(build_frame_line_pfx "$frame_id") EXEC: ${p}" >&2
    local full_prompt="INSTRUCTIONS: ${p}\n$(build_context "$frame_id")\n${p}"
    local result
    result="$(echo -e "$full_prompt" | llm)"
    frames result "$frame_id" "$result" >/dev/null

    # update knowledge in parent frame if there's any new knowledge extracted from the result
    # if [ -n "$parent_id" ] && [ "$parent_id" != "null" ]; then
    #     local knowledge
    #     base_knowledge="$(frames read "$parent_id" knowledge)"
    #     this_knowledge="$(frames read "$frame_id" knowledge)"
    #     # integrate the knowledge using LLM
    #     knowledge="$(echo "INSTRUCTIONS: Integrate the following knowledge in a concise manner.\n\nBase knowledge: ${base_knowledge}\n\nNew knowledge: ${this_knowledge}\n\nIntegrated knowledge:" | llm)"
    #     frames update "$parent_id" knowledge "$knowledge" >/dev/null
    # fi
}

# Create the root frame for this prompt
root_id=$(frames create "$PROMPT")
run $root_id
echo $(frames read "$root_id" result)
