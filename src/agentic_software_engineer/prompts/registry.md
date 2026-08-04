Implement a PromptRegistry that maps FileType enum values to prompt files.

Default mapping:

api -> prompts/coding/api.md
domain -> prompts/coding/domain.md
repository -> prompts/coding/repository.md
configuration -> prompts/coding/configuration.md
test -> prompts/coding/test.md
documentation -> prompts/coding/documentation.md
migration -> prompts/coding/repository.md
infrastructure -> prompts/coding/configuration.md

Requirements:

- Do not use a long if-else chain.
- Allow prompt types to be registered dynamically.
- Reject duplicate registration unless replace=True.
- Raise a custom PromptNotRegisteredError.
- Return pathlib.Path objects.
- Include get(), register(), unregister(), contains(), and list_registered().
- Make the registry thread-safe.
- Add type hints, logging, and docstrings.