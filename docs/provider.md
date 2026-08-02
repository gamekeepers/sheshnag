> **⚠️ SUPERSEDED (2026-07-17).** This note predates issue #13 / PR #20,
> which eliminated the separate Provider identity: organizations own
> workers, and org-scoped worker API keys authenticate daemons. See
> [revised_db_schema.md](revised_db_schema.md) for the current design and
> [v1-spec.md](v1-spec.md) §8 for the authoritative spec. The registration
> payload example below is still accurate; the provider concept and
> `provider/` API prefixes are not.

## Concept map
Organization: is the user/organization with resources at it"s disposal.  Keep it minimalistic for now. LAter on might add invite provider functionality in organization.  
Provider: is a user/organization with resources at it"s disposal.    
Worker: is the machine hosting the resources(in our case, RAM, VRAM) that allows it to run some jobs.   
Daemon: is the background client that runs on the provider's machine and communicates with the platform. Each worker has a daemon associated with it.  
Runtime: is the modality in which the worker can run jobs. Eg. Ollama, vllm, TensorRT, costumized Docker.    


## Provider registration flow:
```
Sign up
↓
Create organization
↓
Generate API key
↓
Download daemon
```

Note: 
1. A provider can have multiple machines(aka workers).
2. Provider will use the API key to authenticate as provider on platform.


## Worker startup
Worker runs locally on the provider's machine.
So can query the capabilities of the provider while registering
Refer [worker_inspection.md](worker_inspection.md)
3. 

```
Worker
↓
POST /worker/register
{
    "name": "",
    "GPU": "",
    "runtime": [
        "ollama",
        "vllm",
        
    ]
}
Authorization:
Provider API Key
```
Backend
```
Worker x belongs to Provider A
Worker x has following capabilities 
```

```
## Recommended registration payload

```json
{
    "hostname": "gpu-box-01",
    "os": "Ubuntu 24.04",
    "cpu": {
        "cores": 16
    },
    "ram": {
        "total_gb": 64
    },
    "gpus": [
        {
            "index": 0,
            "vendor": "nvidia",
            "name": "RTX 4090",
            "vram_gb": 24,
            "driver": "575.64",
            "cuda": "12.8"
        }
    ],
    "runtimes": [
        {
            "type": "ollama",
            "endpoint": "http://localhost:11434",
            "models": [
                "gemma4:27b",
                "qwen3:30b"
            ]
        },
        {
            "type": "vllm",
            "endpoint": "http://localhost:8000",
            "models": [
                "google/gemma-4-27b-it"
            ]
        }
    ]
}
```

So the provider doesn't manually "register" every machine.
The machines self-register.


---

## Rough division of responsibilities
1. backend  dev
update povider/worker apis as per latest provider spec. 
provider/signup
provider/list/workers
worker/register

Suggestions: 
Use prefixes like `provider/` and `worker/` for API endpoints.

2. Daemon developer
Make daemon easy to install via bash script. For user it should be as simple as:    
```bash
curl -sSL https://raw.githubusercontent.com/gamekeepers/moonknight/main/scripts/install.sh | bash
```
Make worker register upon installation.
Query details from machine, package into a json payload, and send it to the backendapi.

3. Frontend :
Provider dashboard with list of workers and their capabilities.

4. Inference : TBA
