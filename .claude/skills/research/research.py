import sys
import os
import json
import hashlib
import time
import urllib.request
import urllib.error

def get_hash(prompt):
    # Remove all whitespace and hash the prompt
    clean_prompt = "".join(prompt.split())
    return hashlib.sha256(clean_prompt.encode('utf-8')).hexdigest()

def call_openai(api_key, prompt):
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "o4-mini-deep-research",
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
        "background": True
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"HTTP Error {e.code}: {e.read().decode('utf-8')}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

def check_status(api_key, response_id):
    url = f"https://api.openai.com/v1/responses/{response_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _d = os.path.abspath(__file__)
        for _ in range(4):
            _d = os.path.dirname(_d)
        key_file = os.path.join(_d, ".temp", "openai.env")
        if os.path.isfile(key_file):
            with open(key_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("OPENAI_API_KEY="):
                        api_key = line[len("OPENAI_API_KEY="):]
                        break
    if not api_key:
        print(json.dumps({"error": "OPENAI_API_KEY not set and not found in .temp/openai.env"}))
        sys.exit(1)

    if len(sys.argv) < 2:
        print(json.dumps({"error": "No prompt provided"}))
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    prompt_hash = get_hash(prompt)
    
    # .claude/skills/research/ → project root is 3 levels up
    _root = os.path.abspath(__file__)
    for _ in range(4):
        _root = os.path.dirname(_root)
    cache_dir = os.path.join(_root, ".files", "research")
    
    # Ensure cache directory exists
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_file = os.path.join(cache_dir, f"{prompt_hash}.json")

    response_data = None
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            response_data = json.load(f)
    else:
        response_data = call_openai(api_key, prompt)
        with open(cache_file, 'w') as f:
            json.dump(response_data, f)

    response_id = response_data.get("id")
    if not response_id:
        print(json.dumps(response_data))
        sys.exit(1)

    # Polling loop
    while True:
        status_data = check_status(api_key, response_id)
        if status_data.get("status") == "completed":
            print(json.dumps(status_data))
            break
        elif status_data.get("status") == "failed":
            print(json.dumps(status_data))
            sys.exit(1)
        
        sys.stderr.write(f"Status: {status_data.get('status', 'unknown')} — polling again in 30s\n")
        time.sleep(30)

if __name__ == "__main__":
    main()
