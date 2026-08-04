Responsibilities:

- Load UTF-8 prompt files using pathlib.
- Cache prompt contents by absolute path.
- Detect file modification time and automatically reload changed prompts.
- Raise PromptFileNotFoundError for missing prompts.
- Reject empty prompt files.
- Provide load(), clear_cache(), and preload() methods.
- Be thread-safe.
- Use dependency injection where practical.
- Add logging and type hints.