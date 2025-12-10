# NMP - Neil's Manual Proxy

A simple, file-based proxy system that enables HTTP request/response synchronization through shared folder structures. Perfect for environments where direct network calls are difficult but file synchronization services (like OneDrive, Dropbox, or network shares) are available.

## Features

### Intelligent Data Handling
- **JSON Data**: Automatically detects and stores JSON as native objects (not strings)
- **Text Data**: Handles plain text content efficiently
- **Binary Data**: Base64 encodes binary content for safe storage

### Flexible Caching
- **Default**: Caches ALL responses (including errors) for offline replay, ignores authorization headers for broader cache sharing
- **Picky Mode**: Use `--cache-picky` to only cache GET/POST requests with 200-299 status codes, includes authorization headers for user-specific caching

### File-Based Architecture
- Uses three folders: `drafts`, `inbox`, and `sent`
- SHA256 hashing ensures request uniqueness
- Human-readable JSON message files with descriptive filenames
- Filenames include flattened URL path for easy identification

## Quick Start

1.  **Set Environment Variables**: Configure your environments in a `.env` file or your shell.

    ```bash
    # Base directory for all NMP folders
    export NMP_BASE="/path/to/shared/nmp"

    # Comma-separated list of environments
    export NMP_FOLDERS=dev,qa,prod

    # Backend URL for each environment
    export NMP_DEV=https://api.dev.example.com
    export NMP_QA=https://api.qa.example.com
    export NMP_PROD=https://api.prod.example.com
    ```

2.  **Run the Server**:

    ```bash
    python NMP.py --server
    ```

3.  **Run the Client**:

    ```bash
    python NMP.py --client --port 19790
    ```

4.  **Make a Request**: Route requests through the client by prefixing the path with the desired folder name.

    ```bash
    # This request will be processed by the 'dev' environment
    curl http://localhost:19790/dev/users/123

    # This request will go to the 'qa' environment
    curl http://localhost:19790/qa/status
    ```

## Command-Line Options

| Option | Description |
|---|---|
| `--client` | Run in client mode. |
| `--server` | Run in server mode. |
| `-p, --port` | (Client only) Port to listen on. Default: `19790`. |
| `--cache-picky` | (Client only) Cache only successful GET/POST requests. |

## Message Format

NMP stores requests and responses as JSON files with intelligent data typing:

```json
{
  "method": "POST",
  "path": "api/users",
  "headers": {
    "content-type": "application/json"
  },
  "payload": {
    "name": "John Doe",
    "email": "john@example.com"
  },
  "type": "json",
  "stats": {
    "created_at": "2025-06-30T18:45:24.123Z",
    "started_at": "2025-06-30T18:45:24.200Z",
    "finished_at": "2025-06-30T18:45:24.456Z",
    "elapsed_total": 0.333,
    "elapsed_request": 0.256
  },
  "original_headers": {
    "content-type": "application/json",
    "authorization": "Bearer abc123...",
    "user-agent": "curl/7.68.0",
    "accept": "*/*"
  },
  "response": {
    "status_code": 201,
    "status_text": "Created",
    "headers": {
      "content-type": "application/json"
    },
    "payload": {
      "id": 123,
      "name": "John Doe",
      "email": "john@example.com"
    },
    "type": "json"
  }
}
```

## Data Types

| Type | Description | Example |
|------|-------------|---------|
| `json` | Valid JSON data stored as objects | `{"key": "value"}` |
| `string` | Plain text content | `"Hello World"` |
| `base64` | Binary data encoded as base64 | `"iVBORw0KGgoAAAANSUhEUgAA..."` |

## Timing Statistics

NMP provides detailed timing information in the `stats` section:

| Field | Description | When Set |
|-------|-------------|----------|
| `created_at` | ISO timestamp when JSON message was created | Client (when request received) |
| `started_at` | ISO timestamp when server picked up the request | Server (after moving to inbox) |
| `finished_at` | ISO timestamp when server completed processing | Server (after getting response) |
| `elapsed_total` | Total seconds from created to finished | Server (calculated) |
| `elapsed_request` | Server processing time in seconds | Server (calculated) |

### Example Timing Analysis
```json
{
  "stats": {
    "created_at": "2025-06-30T18:45:24.123Z",
    "started_at": "2025-06-30T18:45:24.200Z", 
    "finished_at": "2025-06-30T18:45:24.456Z",
    "elapsed_total": 0.333,    // Total time: 333ms
    "elapsed_request": 0.256   // Server processing: 256ms
  }
}
```

This allows you to analyze:
- **Queue time**: `started_at - created_at` (77ms in example)
- **Processing time**: `elapsed_request` (256ms in example)
- **Total time**: `elapsed_total` (333ms in example)

## Caching Behavior

### Default Mode (Cache Everything)
- Caches ALL responses regardless of status code
- Enables offline replay of error scenarios
- **Ignores authorization headers** for broader cache sharing across users
- Perfect for debugging and development

### Picky Mode (`--cache-picky`)
- Only caches GET and POST requests
- Only caches responses with 200-299 status codes
- **Includes authorization headers** for user-specific caching
- Useful for production-like environments

## Header Handling

The proxy uses different header inclusion strategies based on the caching mode:

### Default Mode Headers
- `content-type`: Always included for proper data handling
- `x-*`: Custom headers starting with 'x-' are included
- **Authorization excluded**: Enables cache sharing between different users/tokens

### Picky Mode Headers  
- `content-type`: Always included for proper data handling
- `authorization`: Included to ensure user-specific caching
- `x-*`: Custom headers starting with 'x-' are included

This design allows:
- **Team sharing**: Default mode lets team members share cached responses
- **User isolation**: Picky mode ensures each user's auth context is respected

### Headers vs Original Headers

The message format includes two header properties:

- **`headers`**: Filtered headers used for cache key generation (affects SHA256 hash)
  - Only includes headers relevant to caching strategy
  - Used by server to make the actual HTTP request
  
- **`original_headers`**: Complete set of all request headers (not used for hashing)
  - Preserves full request context for debugging/analysis
  - Includes all headers sent by the original client
  - Useful for troubleshooting and request inspection

## Use Cases

1. **Development Behind Corporate Firewalls**: Sync requests through shared drives
2. **Offline API Testing**: Cache responses for offline development
3. **Error Scenario Replay**: Capture and replay error conditions
4. **API Documentation**: Generate human-readable request/response logs
5. **Cross-Network Communication**: Bridge different network segments

## Filename Format

NMP creates descriptive filenames that include the flattened URL path for easy identification:

**Format**: `{flattened_path}_{sha256_hash}.json`

### Path Flattening Rules
- Replace all non-alphanumeric or dashes with underscores
- Limit to 100 characters maximum
- Replace multiple underscores with single underscore
- Remove prefixed underscores or dashes
- Empty paths become "root"

### Examples
| Original Path | Flattened | Example Filename |
|---------------|-----------|------------------|
| `/hello/world` | `hello_world` | `hello_world_a1b2c3d4...json` |
| `/cost-data/metrics` | `cost-data_metrics` | `cost-data_metrics_e5f6g7h8...json` |
| `/api/v1/users/123` | `api_v1_users_123` | `api_v1_users_123_i9j0k1l2...json` |
| `/` | `root` | `root_m3n4o5p6...json` |
| `/special@chars!` | `special_chars` | `special_chars_q7r8s9t0...json` |

## File Structure

```
NMP_BASE/
├── dev/
│   ├── drafts/
│   ├── inbox/
│   └── sent/
├── qa/
│   ├── drafts/
│   ├── inbox/
│   └── sent/
└── prod/
    ├── drafts/
    ├── inbox/
    └── sent/
```

## Environment Variables

| Variable | Description | Example |
|---|---|---|
| `NMP_BASE` | **Required.** The root directory where all environment folders are stored. | `/path/to/nmp` |
| `NMP_FOLDERS` | **Required.** A comma-separated list of environment folder names. | `dev,qa,prod` |
| `NMP_{FOLDER}` | **Required.** The remote server URL for each folder specified in `NMP_FOLDERS`. | `NMP_DEV=https://api.dev.example.com` |

## Requirements

- Python 3.7+
- Flask
- Requests

## License

MIT License - see LICENSE file for details.
