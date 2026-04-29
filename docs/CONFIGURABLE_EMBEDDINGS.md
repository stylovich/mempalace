# Configurable Embedding Models

MemPalace defaults to ChromaDB's local `all-MiniLM-L6-v2` ONNX embedding
model. That keeps the default install small and compatible with existing
palaces.

For multilingual or instruction-tuned retrieval, MemPalace can also run
local SentenceTransformer models such as `Qwen/Qwen3-Embedding-0.6B`.
Changing the model or output dimension changes the vector shape and retrieval
space, so rebuild the Chroma collection before using an existing palace with a
new embedding configuration.

## Install From This Branch

Until this support is released on PyPI, install from the fork branch:

```bash
python -m venv ~/.venvs/mempalace
~/.venvs/mempalace/bin/pip install \
  "git+https://github.com/stylovich/mempalace.git@feature/configurable-embedding-model[sentence-transformers]"
```

After the feature is released, install the extra from PyPI instead:

```bash
pip install "mempalace[sentence-transformers]"
```

The first run downloads model files into the Hugging Face cache, usually under
`~/.cache/huggingface`. Make sure the machine has network access and enough
disk space for the selected model.

## Qwen 0.6B Configuration

Set the global MemPalace config in `~/.mempalace/config.json`:

```json
{
  "palace_path": "~/.mempalace/palace",
  "collection_name": "mempalace_drawers",
  "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
  "embedding_dimension": 1024,
  "embedding_device": "auto",
  "embedding_query_instruction": "Given a memory search query, retrieve relevant saved memories, project decisions, code context, and conversations that answer the query."
}
```

Configuration keys:

| Key | Default | Description |
|-----|---------|-------------|
| `embedding_model` | `default` | `default` keeps ChromaDB's ONNX MiniLM model. Any other value is treated as a SentenceTransformer model id. |
| `embedding_dimension` | model default | Optional output dimension for Matryoshka-style models. Use `1024` for `Qwen/Qwen3-Embedding-0.6B`. |
| `embedding_device` | `auto` | `auto`, `cpu`, or `cuda`. Unsupported ONNX-only values such as `coreml` and `dml` fall back to CPU for SentenceTransformer models. |
| `embedding_query_instruction` | memory retrieval instruction | Prompt used for query embeddings when the model supports instruction-style retrieval. |

The same settings can be supplied with environment variables:

```bash
export MEMPALACE_EMBEDDING_MODEL="Qwen/Qwen3-Embedding-0.6B"
export MEMPALACE_EMBEDDING_DIMENSION="1024"
export MEMPALACE_EMBEDDING_DEVICE="auto"
export MEMPALACE_EMBEDDING_QUERY_INSTRUCTION="Given a memory search query, retrieve relevant saved memories, project decisions, code context, and conversations that answer the query."
```

## Rebuild Existing Palaces

Do not mix embeddings from different models or dimensions in the same ChromaDB
collection. For example, MiniLM uses 384-dimensional vectors while Qwen 0.6B
with `embedding_dimension=1024` writes 1024-dimensional vectors.

If the palace was created with another embedding model, rebuild it after
changing the config. For test installs with disposable memory, the simplest
path is to move the old palace away and mine again:

```bash
mv ~/.mempalace/palace ~/.mempalace/palace.minilm-backup
mempalace mine /path/to/project --wing myproject
```

If you keep multiple machines, each machine must use the same embedding model
and dimension for any shared or synchronized palace.

## Codex MCP Setup

Point Codex at the `mempalace-mcp` executable from the environment where
MemPalace was installed:

```toml
[mcp_servers.mempalace]
command = "/home/you/.venvs/mempalace/bin/mempalace-mcp"
```

If you want Codex to use only MemPalace memories, disable Codex's native
memory system in the Codex config:

```toml
[memories]
use_memories = false
generate_memories = false
```

## Verify

Confirm the installed package and active embedding settings:

```bash
~/.venvs/mempalace/bin/python - <<'PY'
import mempalace
from mempalace.config import MempalaceConfig

cfg = MempalaceConfig()
print(mempalace.__file__)
print(cfg.embedding_model)
print(cfg.embedding_dimension)
print(cfg.embedding_device)
PY
```

Then add and search a test memory through the MCP server or CLI:

```bash
mempalace search "test memory"
```

If search returns dimension errors from ChromaDB, the collection was created
with a different model or dimension and needs to be rebuilt.
