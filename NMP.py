#!/usr/bin/env python3
"""
NMP.py - Neil's Manual Proxy

This script implements a simple, file-based proxy system that can be run in
one of two modes: 'client' or 'server'. It facilitates communication by using
a shared directory structure, making it suitable for environments where direct
network calls are difficult but file synchronization services (like OneDrive,
Dropbox, or a network share) are available.

Core Concept:
The entire process is orchestrated through files in three key folders:
1. 'drafts': The client writes new, outgoing requests here.
2. 'inbox': The server moves files from 'drafts' to here to signal that a
            request is being actively processed.
3. 'sent': The server writes the final, complete transaction (original request
           plus the remote server's response) here. The client retrieves the
           response from this folder.

Data Handling:
The proxy intelligently handles different data types:
- JSON data: Stored as native JSON objects with type "json"
- Text data: Stored as strings with type "string"  
- Binary data: Base64 encoded with type "base64"

Caching Behavior:
By default, ALL responses are cached (including errors) to enable offline replay
of error scenarios. Use --cache-picky to only cache GET/POST requests with 
200-299 status codes.

--- Client Mode (`--client`) ---
When run as a client, this script starts a Flask web server that listens for
local HTTP requests.

Workflow:
1. Receives an HTTP request (e.g., from `curl`).
2. Packages the request's method, path, headers, and body into a JSON object.
3. Calculates the SHA256 hash of this JSON object.
4. Saves the JSON object to a file named `<hash>.json` in the 'draft-folder'.
5. Checks for cached responses in 'sent-folder' and returns them if found
   (subject to caching policy).
6. If no cache hit, waits for the draft file to be removed (indicating the 
   server has picked it up).
7. Then, it waits for a file with the same name (`<hash>.json`) to appear in
   the 'sent-folder'.
8. Once the response file appears, it's loaded, and the contents (status code,
   headers, and body from the remote server) are returned to the original
   HTTP caller.

--- Server Mode (`--server`) ---
When run as a server, this script continuously monitors the 'draft-folder' for
new request files.

Workflow:
1. Detects a new `.json` file in the 'draft-folder'.
2. Moves the file to the 'inbox-folder' to prevent re-processing and signal
   that the request is being handled.
3. Parses the JSON file to get the request details (method, path, etc.).
4. Forwards this request to the configured 'remote-server'.
5. Receives the response from the remote server.
6. Appends the response details (status code, headers, body) to the original
   JSON object with intelligent data type detection.
7. Saves this updated JSON object to the 'sent-folder' using the original
   filename (<hash>.json).
8. Deletes the corresponding file from the 'inbox-folder' to complete the cycle.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, request
from dotenv import load_dotenv


# --- Curl Template Executor ---

class CurlTemplateExecutor:
  """
  Executes HTTP requests via a shell-based curl template instead of using
  the requests library. This is useful for environments where requests must
  be routed through special infrastructure (e.g., Azure Container exec).
  
  The template file should contain placeholders:
    {{METHOD}} - HTTP method (GET, POST, etc.)
    {{URL}} - Full URL including query string
    {{HEADERS}} - Curl header arguments (e.g., -H "Content-Type: application/json")
    {{DATA}} - Curl data argument (e.g., -d '{"key": "value"}')
    {{CURL_OPTS}} - Combined curl options string
  
  Example template:
    script_name="curl_script_$(date +%s%N).sh"
    _=$(az container exec --resource-group "my-resource-group" --name "my-container" --exec-command "/bin/sh" <<EOF
    cat > /tmp/${script_name} << 'SCRIPT'
    #!/bin/sh
    curl -sk {{CURL_OPTS}} {{URL}}
    SCRIPT
    chmod +x /tmp/${script_name}
    exit
    EOF
    2>&1)
    output=$(az container exec --resource-group "my-resource-group" --name "my-container" --exec-command "/tmp/${script_name}")
    echo "$output"
  """
  
  def __init__(self, template_path):
    """
    Initialize with the path to the template file.
    """
    self.template_path = template_path
    self.template = self._load_template()
  
  def _load_template(self):
    """Load the template from the file."""
    try:
      with open(self.template_path, 'r') as f:
        return f.read()
    except FileNotFoundError:
      print(f"ERROR: Curl template file not found: {self.template_path}", file=sys.stderr)
      sys.exit(1)
    except Exception as e:
      print(f"ERROR: Failed to load curl template: {e}", file=sys.stderr)
      sys.exit(1)
  
  def _build_curl_opts(self, method, headers, data):
    """
    Build curl command options from request parameters.
    Returns a string of curl options.
    """
    opts = ['-v']  # Add verbose for response headers and status
    
    # Method
    if method.upper() != 'GET':
      opts.append(f'-X {method.upper()}')
    
    # Headers
    if headers:
      for key, value in headers.items():
        # Escape single quotes in header values
        escaped_value = value.replace("'", "'\\''")
        opts.append(f"-H '{key}: {escaped_value}'")
    
    # Data/body
    if data:
      # Escape single quotes in data
      if isinstance(data, bytes):
        try:
          data = data.decode('utf-8')
        except UnicodeDecodeError:
          # For binary data, base64 encode it
          data = base64.b64encode(data).decode('utf-8')
      escaped_data = data.replace("'", "'\\''")
      opts.append(f"-d '{escaped_data}'")
    
    return ' '.join(opts)
  
  def _build_headers_string(self, headers):
    """Build just the headers portion as curl -H arguments."""
    if not headers:
      return ''
    parts = []
    for key, value in headers.items():
      escaped_value = value.replace("'", "'\\''")
      parts.append(f"-H '{key}: {escaped_value}'")
    return ' '.join(parts)
  
  def _build_data_string(self, data):
    """Build just the data portion as a curl -d argument."""
    if not data:
      return ''
    if isinstance(data, bytes):
      try:
        data = data.decode('utf-8')
      except UnicodeDecodeError:
        data = base64.b64encode(data).decode('utf-8')
    escaped_data = data.replace("'", "'\\''")
    return f"-d '{escaped_data}'"
  
  def execute(self, method, url, headers=None, data=None):
    """
    Execute an HTTP request using the curl template.
    
    Args:
      method: HTTP method (GET, POST, PUT, DELETE, etc.)
      url: Full URL to request
      headers: Dict of headers to send
      data: Optional request body (str, bytes)

    Returns:
      dict with keys: status_code, status_text, headers, body
    """
    headers = headers or {}
    data = data or ''

    curl_opts = self._build_curl_opts(method, headers, data)
    headers_str = self._build_headers_string(headers)
    data_str = self._build_data_string(data)

    replacements = {
      'METHOD': method.upper(),
      'URL': shlex.quote(url),
      'HEADERS': headers_str,
      'DATA': data_str,
      'CURL_OPTS': curl_opts
    }

    rendered = self.template
    for key, value in replacements.items():
      rendered = rendered.replace(f"{{{{{key}}}}}", value)

    print(f"Executing curl template for {method} {url}:\n```sh\n{rendered}\n```\n", file=sys.stderr)
    result = subprocess.run(rendered, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
      raise RuntimeError(f"Curl template failed (exit {result.returncode}): {result.stderr or result.stdout}")

    output = (result.stdout or '').strip() + (result.stderr or '').strip()
    status_code, status_text, parsed_headers, body = self._parse_curl_output(output)
    print(f"Curl response: {status_code} {status_text}, headers: {parsed_headers.keys()}", file=sys.stderr)
    return {
      'status_code': status_code,
      'status_text': status_text,
      'headers': parsed_headers,
      'body': body,
    }

  def _parse_curl_output(self, output):
    """
    Try to extract status, headers, and body from curl stderr.
    If no HTTP status line is present, default to 204
    """
    status_code = None
    status_text = ''
    headers = {}
    debug_lines = []
    body_lines = []

    for line in output.splitlines():
      line = line.strip()  # remove \r
      if not line or line in {'*', '<', '>'}:
        continue

      if line.startswith('* '):  # debug print
        debug_lines.append(line)
        print(f"DEBUG: {line}", file=sys.stderr)
        continue

      if line.startswith('> '):  # request header
        debug_lines.append(line)
        continue

      if line.startswith('< HTTP/'):
        # Reset for the last response block (handles redirects)
        parts = line.split(" ", 3)
        status_code = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        status_text = parts[3] if len(parts) > 3 else ''
        print(f"STATUS: {status_code} {status_text}", file=sys.stderr)
        continue

      if line.startswith('< '):  # response header
        line = line[2:]  # strip leading '< '
        header_name = line.split(':', 1)[0].strip().lower()
        header_value = line.split(':', 1)[1].strip() if ':' in line else ''
        headers[header_name] = header_value
        print(f"HEADER: {header_name}: {header_value}", file=sys.stderr)
        continue

      # Anything else is body
      print(f"BODY: {line}", file=sys.stderr)
      body_lines.append(line)

    if status_code is None:
      status_code = 204

    return status_code, status_text, headers, '\n'.join(body_lines)


def load_folder_config():
  """
  Parse .env and environment to build folder configuration for all environments
  Returns:
    config = {
      'base': abs_path_to_nmp_base,
      'folders': ['dev', 'qa', ...],
      'remote_servers': {
        'dev': ...,
        ...
      },
      'drafts': {'dev': ..., ...},
      'inbox': {...},
      'sent': {...}
      }
  """
  nmp_base = os.getenv('NMP_BASE') or default_NMP_BASE()
  if not nmp_base:
    print('ERROR: NMP_BASE is not set.')
    sys.exit(1)
  nmp_base = os.path.expanduser(nmp_base)
  nmp_folders = os.getenv('NMP_FOLDERS', '')
  folders = [f.strip() for f in nmp_folders.split(',') if f.strip()]
  if not folders:
    print('ERROR: No NMP_FOLDERS specified in env; e.g. NMP_FOLDERS=dev,qa,prod')
    sys.exit(1)
  # For each folder, collect backend URLs
  remote_servers = {}
  drafts, inbox, sent = {}, {}, {}
  curl_templates = {}
  default_curl_template = os.getenv('NMP_CURL_TEMPLATE')
  for folder in folders:
    envvar = f'NMP_{folder.upper()}'
    remote_url = os.getenv(envvar)
    if not remote_url:
      print(f'ERROR: No {envvar} (remote server URL) set for folder {folder}')
      sys.exit(1)
    remote_servers[folder] = remote_url
    drafts[folder] = os.path.join(nmp_base, folder, 'drafts')
    inbox[folder] = os.path.join(nmp_base, folder, 'inbox')
    sent[folder] = os.path.join(nmp_base, folder, 'sent')
    folder_template = os.getenv(f'NMP_{folder.upper()}_CURL_TEMPLATE', default_curl_template)
    if folder_template:
      curl_templates[folder] = os.path.expanduser(folder_template)
  return {
    'base': nmp_base,
    'folders': folders,
    'remote_servers': remote_servers,
    'drafts': drafts,
    'inbox': inbox,
    'sent': sent,
    'curl_templates': curl_templates
  }

# Global variables for header handling
HASH_HEADERS = ['content-type']  # Headers used for cache key generation
HASH_HEADERS_PICKY = ['authorization', 'xproxy-api-key', 'api-key']  # Additional headers for picky caching
PASS_HEADERS = ['content-type', 'authorization']  # Headers passed to server

# --- Helper Functions ---

def flatten_path_for_filename(path):
  """
  Convert a URL path to a flattened filename-safe string.
  
  Rules:
  - Replace all non-alphanumeric or dashes with underscores
  - Limit to up to 100 characters
  - Replace multiple underscores in a row with a single underscore
  - Remove any prefixed underscores or dashes (so it always starts with alphanumerics)
  """
  import re
  
  # Replace all non-alphanumeric or dashes with underscores
  flattened = re.sub(r'[^a-zA-Z0-9\-]', '_', path)
  
  # Replace multiple underscores in a row with a single underscore
  flattened = re.sub(r'_+', '_', flattened)
  
  # Remove any prefixed underscores or dashes
  flattened = re.sub(r'^[_\-]+', '', flattened)
  
  # Limit to 100 characters
  flattened = flattened[:100]
  
  # If empty after cleaning, use a default
  if not flattened:
    flattened = 'root'
  
  return flattened

# --- Data Serialization Helpers ---

def serialize_data(data):
  """
  Intelligently serialize data as JSON, string, or base64 based on content.
  Returns a tuple of (serialized_data, type_indicator).
  """
  if data is None or data == '':
    return '', 'string'
  
  # If data is bytes, try to decode as text first
  if isinstance(data, bytes):
    try:
      # Try to decode as UTF-8 text
      text_data = data.decode('utf-8')
      # Now check if the decoded text is valid JSON
      try:
        parsed_json = json.loads(text_data)
        return parsed_json, 'json'  # Return the parsed object, not the string
      except (json.JSONDecodeError, ValueError):
        # Not valid JSON, treat as string
        return text_data, 'string'
    except UnicodeDecodeError:
      # Cannot decode as UTF-8, must be binary data
      return base64.b64encode(data).decode('utf-8'), 'base64'
  
  # If data is a string, try to parse as JSON first
  if isinstance(data, str):
    try:
      # Try to parse as JSON to validate it's valid JSON
      parsed_json = json.loads(data)
      return parsed_json, 'json'  # Return the parsed object, not the string
    except (json.JSONDecodeError, ValueError):
      # Not valid JSON, treat as string
      return data, 'string'
  
  # For other types (dict, list, etc.), they're already JSON-serializable
  try:
    # Test if it can be JSON serialized
    json.dumps(data)
    return data, 'json'  # Return the object as-is
  except (TypeError, ValueError):
    # If JSON serialization fails, convert to string
    return str(data), 'string'


def default_NMP_BASE():
  """Use $HOME/Downloads/nmp/"""
  path = os.path.join(os.path.expanduser('~'), 'Downloads', 'nmp')
  os.makedirs(path, exist_ok=True)
  return path

def deserialize_data(data, data_type):
  """
  Deserialize data based on the type indicator.
  Returns the original data in its appropriate format.
  """
  if data_type == 'base64':
    return base64.b64decode(data.encode('utf-8'))
  elif data_type == 'json':
    # JSON data is now stored as actual objects, not strings
    if isinstance(data, (dict, list)):
      # Already a parsed object, serialize it to JSON string for transmission
      return json.dumps(data)
    else:
      # Fallback: try to parse as JSON string (for backward compatibility)
      try:
        return json.dumps(json.loads(data))
      except (json.JSONDecodeError, ValueError):
        return str(data)
  else:  # data_type == 'string' or unknown
    return data

# --- Client Mode ---

def run_client(folder_config, port=19790, cache_picky=False):
  """
  Flask app that dispatches requests to the appropriate NMP folder based on path prefix.
  """
  app = Flask(__name__)
  nmp_folders = folder_config['folders']
  drafts = folder_config['drafts']
  sent = folder_config['sent']

  @app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
  @app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
  def handle_request(path):
    # Expect /<folder>/<path> routing
    path_parts = path.strip('/').split('/') if path else []
    if len(path_parts) < 1 or not path_parts[0]:
      return {'error': 'Please specify NMP folder as the first path part.'}, 400
    folder = path_parts[0]
    if folder not in nmp_folders:
      return {'error': f'Unknown folder {folder}.'}, 404
    subpath = '/'.join(path_parts[1:])

    # Separate headers for hashing vs passing to server
    hash_headers = HASH_HEADERS.copy()
    if cache_picky:
      hash_headers.extend(HASH_HEADERS_PICKY)
    pass_headers = PASS_HEADERS.copy()

    # Filter headers
    allowed_headers = {}
    for key, value in request.headers.items():
      key_lower = key.lower()
      if key_lower in hash_headers or key_lower.startswith('x-') or key_lower.startswith('xproxy-'):
        allowed_headers[key] = value
      else:
        print(f"Skipping header for hash: {key}", file=sys.stderr)
    server_headers = {}
    for key, value in request.headers.items():
      key_lower = key.lower()
      if key_lower in pass_headers or key_lower.startswith('x-') or key_lower.startswith('xproxy-'):
        server_headers[key] = value
      else:
        print(f"Skipping header for server: {key}", file=sys.stderr)

    # Serialize body
    raw_data = request.get_data()
    if raw_data:
      payload_data, payload_type = serialize_data(raw_data)
    else:
      payload_data, payload_type = '', 'string'

    # Capture query string
    query_string = request.query_string.decode('utf-8') if request.query_string else ''

    # Core request object
    request_core = {
      'method': request.method,
      'path': subpath,
      'query_string': query_string,
      'folder': folder,
      'headers': allowed_headers,
      'payload': payload_data,
      'type': payload_type
    }
    request_json_core = json.dumps(request_core, indent=2)
    sha256_hash = hashlib.sha256(request_json_core.encode('utf-8')).hexdigest()

    # File naming
    path_prefix = flatten_path_for_filename(subpath)
    filename = f"{path_prefix}_{sha256_hash}.json"
    draft_filepath = Path(drafts[folder]) / filename
    sent_filepath = Path(sent[folder]) / filename

    # Check cached response
    if sent_filepath.exists():
      response_data = json.loads(sent_filepath.read_text())
      response_info = response_data.get('response', {})
      status_code = response_info.get('status_code')
      should_use_cache = False
      if cache_picky:
        if (request.method in ['GET', 'POST'] and status_code and 200 <= status_code <= 299):
          should_use_cache = True
      else:
        if status_code is not None:
          should_use_cache = True
      if should_use_cache:
        from flask import Response
        payload_data = response_info.get('payload', '')
        payload_type = response_info.get('type', 'string')
        payload = deserialize_data(payload_data, payload_type)
        headers = response_info.get('headers', {})
        headers.pop('Content-Encoding', None)
        headers.pop('Transfer-Encoding', None)
        return Response(payload, status=status_code, headers=headers)

    # Save request
    request_data = request_core.copy()
    request_data['stats'] = {'created_at': datetime.now(timezone.utc).isoformat()}
    request_data['original_headers'] = dict(request.headers.items())
    request_data['server_headers'] = server_headers
    draft_filepath.parent.mkdir(parents=True, exist_ok=True)
    draft_filepath.write_text(json.dumps(request_data, indent=2))

    # Wait for processing
    while draft_filepath.exists():
      time.sleep(0.5)
    while not sent_filepath.exists():
      time.sleep(0.5)

    # Load response
    response_data = json.loads(sent_filepath.read_text())
    response_info = response_data.get('response', {})
    from flask import Response
    payload_data = response_info.get('payload', '')
    payload_type = response_info.get('type', 'string')
    payload = deserialize_data(payload_data, payload_type)
    status_code = response_info.get('status_code', 500)
    headers = response_info.get('headers', {})
    headers.pop('Content-Encoding', None)
    headers.pop('Transfer-Encoding', None)
    return Response(payload, status=status_code, headers=headers)

  route_examples = [f"/{folder}/..." for folder in nmp_folders]
  print(f"NMP Client listening on http://0.0.0.0:{port}. Route into: {', '.join(route_examples)}")
  app.run(host='0.0.0.0', port=port)

# --- Server Mode ---

def process_server_request(file_path, remote_server, sent_folder, inbox_folder, curl_executor=None):
  """
  Processes a single request file found by the server.
  """
  original_hash_name = file_path.name
  inbox_filepath = Path(inbox_folder) / original_hash_name
  sent_filepath = Path(sent_folder) / original_hash_name

  try:
    # 1. Move draft to inbox and record start time
    started_at = datetime.now(timezone.utc)
    shutil.move(file_path, inbox_filepath)
    print(f"Server: Moved {original_hash_name} to inbox.", file=sys.stderr)

    # 2. Load and parse the request
    request_data = json.loads(inbox_filepath.read_text())
    
    method = request_data['method']
    path = request_data['path']
    query_string = request_data.get('query_string', '')
    # Use server_headers if available, fallback to headers for backward compatibility
    headers = request_data.get('server_headers', request_data.get('headers', {}))
    payload_data = request_data.get('payload')
    payload_type = request_data.get('type', 'string')
    
    # Deserialize the request payload based on its type
    if payload_data:
      payload = deserialize_data(payload_data, payload_type)
    else:
      payload = None
    
    # Clean up headers for the requests library
    headers.pop('Host', None)
    headers.pop('Content-Length', None)
    
    # Build URL with query string
    url = f"{remote_server}/{path}"
    if query_string:
      url += f"?{query_string}"

    # 3. Make the request to the remote server
    print(f"Server: {method} {url}", file=sys.stderr)
    if curl_executor:
      print(f"Server: Using curl template executor.", file=sys.stderr)
      curl_response = curl_executor.execute(
        method=method,
        url=url,
        headers=headers,
        data=payload if payload else None
      )
      response_content = curl_response.get('body', '')
      response_status_code = curl_response.get('status_code', 200)
      response_status_text = curl_response.get('status_text', '')
      response_headers = curl_response.get('headers', {})
    else:
      response = requests.request(
          method=method,
          url=url,
          headers=headers,
          data=payload if payload else None,
          allow_redirects=False
      )
      response_content = response.content
      response_status_code = response.status_code
      response_status_text = response.reason
      response_headers = dict(response.headers)
    
    # 4. Construct the full response object
    finished_at = datetime.now(timezone.utc)
    
    # Serialize the response data appropriately
    try:
      if response_content:
        response_payload, response_type = serialize_data(response_content)
      else:
        response_payload, response_type = '', 'string'
    except Exception:
      # Fallback to text if content access fails
      response_payload, response_type = serialize_data(str(response_content))
    
    request_data['response'] = {
      'status_code': response_status_code,
      'status_text': response_status_text,
      'headers': response_headers,
      'payload': response_payload,
      'type': response_type
    }

    # Update stats with detailed timing information
    if 'stats' not in request_data:
        request_data['stats'] = {}
    
    # Add server-side timing
    request_data['stats']['started_at'] = started_at.isoformat()
    request_data['stats']['finished_at'] = finished_at.isoformat()
    
    # Calculate elapsed times
    created_at_str = request_data['stats'].get('created_at')
    if created_at_str:
        try:
            created_at = datetime.fromisoformat(created_at_str)
            request_data['stats']['elapsed_total'] = (finished_at - created_at).total_seconds()
        except (ValueError, TypeError):
            pass  # Ignore if timestamp is missing or malformed
    
    request_data['stats']['elapsed_request'] = (finished_at - started_at).total_seconds()

    # 5. Save the complete data to the sent folder
    sent_filepath.write_text(json.dumps(request_data, indent=2))
    print(f"Server: Wrote response to {sent_filepath}", file=sys.stderr)

  except Exception as e:
    print(f"Server: Error processing {original_hash_name}: {e}", file=sys.stderr)
    # Optionally, write an error file to the sent folder
    error_data = {
      "error": str(e)
    }
    sent_filepath.write_text(json.dumps(error_data, indent=2))
  
  finally:
    # 6. Delete the file from the inbox
    if inbox_filepath.exists():
      inbox_filepath.unlink()
      print(f"Server: Deleted {inbox_filepath.name} from inbox.", file=sys.stderr)


def run_server(folder_config):
  """
  Monitor all draft folders. Each folder uses its own backend server.
  """
  print("NMP Server running...")
  folders = folder_config['folders']
  drafts = folder_config['drafts']
  inbox = folder_config['inbox']
  sent = folder_config['sent']
  backends = folder_config['remote_servers']
  curl_templates = folder_config.get('curl_templates', {})
  curl_executors = {}
  for folder in folders:
    Path(drafts[folder]).mkdir(parents=True, exist_ok=True)
    Path(inbox[folder]).mkdir(parents=True, exist_ok=True)
    Path(sent[folder]).mkdir(parents=True, exist_ok=True)
    template_path = curl_templates.get(folder)
    if template_path:
      curl_executors[folder] = CurlTemplateExecutor(template_path)
    print(f"  - {folder}: drafts={drafts[folder]}, inbox={inbox[folder]}, sent={sent[folder]}, backend={backends[folder]}, curl_template={template_path or 'requests'}")
  print()
  while True:
    for folder in folders:
      for file_path in Path(drafts[folder]).glob('*.json'):
        process_server_request(
          file_path,
          backends[folder],
          sent[folder],
          inbox[folder],
          curl_executor=curl_executors.get(folder)
        )
    time.sleep(1)

# --- CLI Entrypoint ---

# --- Main Execution ---

if __name__ == "__main__":
  load_dotenv()
  parser = argparse.ArgumentParser(description="NMP: Neil's Manual Proxy aka folderized client/server.")
  mode_group = parser.add_mutually_exclusive_group(required=True)
  mode_group.add_argument('--client', action='store_true', help='Run as HTTP file proxy (client mode)')
  mode_group.add_argument('--server', action='store_true', help='Run as batch processor/relay (server mode)')
  parser.add_argument('-p', '--port', type=int, default=19790, help='Port for the client HTTP server (default 19790)')
  parser.add_argument('--cache-picky', action='store_true', help='Only cache GET/POST requests with 200-299 status codes. By default, all responses are cached.')
  args = parser.parse_args()

  folder_config = load_folder_config()
  for key in ['drafts', 'inbox', 'sent']:
    for path in folder_config[key].values():
      Path(path).mkdir(parents=True, exist_ok=True)

  if args.client:
    run_client(folder_config, port=args.port, cache_picky=args.cache_picky)
  elif args.server:
    run_server(folder_config)
