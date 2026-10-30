# Providers & Models API

List and manage AI providers and models.

|Endpoint|Method|Name|Description|API Details URL|
|-|-|-|-|-|
|`/api/providers/models`|GET|List Provider Models||get_api_providers_models.md|
|`/api/available_models`|GET|List Available Models||get_api_available_models.md|
|`/api/providers/embedding_models`|GET|List Provider Embedding Models||get_api_providers_embedding_models.md|
|`/api/available_embedding_models`|GET|List Available Embedding Models||get_api_available_embedding_models.md|
|`/api/providers/reranker_models`|GET|List Provider Reranker Models||get_api_providers_reranker_models.md|
|`/api/available_reranker_models`|GET|List Available Reranker Models||get_api_available_reranker_models.md|
|`/api/provider/ollama/connect`|POST|Connect Ollama||post_api_provider_ollama_connect.md|
|`/api/provider/docker_model_runner/connect`|POST|Connect Docker Model Runner||post_api_provider_docker_model_runner_connect.md|
|`/api/provider/openai_compatible`|POST|Add OpenAI Compatible Provider||post_api_provider_openai_compatible.md|
|`/api/provider/openai_compatible`|DELETE|Delete OpenAI Compatible Provider||delete_api_provider_openai_compatible.md|
|`/api/settings/available_providers`|GET|List Available Providers|Returns all providers that can have custom models added.|get_api_settings_available_providers.md|
|`/api/settings/user_models`|GET|List User Models|Returns all user-defined models (new registry + legacy combined).  Includes both user_model_registry...|get_api_settings_user_models.md|
|`/api/settings/user_models`|POST|Add User Model|Add a user-defined model to the registry.|post_api_settings_user_models.md|
|`/api/settings/user_models`|DELETE|Delete User Model|Delete a user-defined model from the registry.  Supports two deletion methods: 1. By ID (new): Pass ...|delete_api_settings_user_models.md|
|`/api/provider/connect_api_key`|POST|Connect Provider API Key||post_api_provider_connect_api_key.md|
|`/api/provider/disconnect_api_key`|POST|Disconnect Provider API Key||post_api_provider_disconnect_api_key.md|
|`/api/provider/create_kiln_copilot_api_key`|POST|Create Kiln Copilot API Key||post_api_provider_create_kiln_copilot_api_key.md|
|`/api/provider/verify_kiln_copilot_api_key`|GET|Verify Kiln Copilot API Key|Verify the stored Kiln Copilot API key against the Kiln server.  Returns `{is_valid: bool}`. A stale...|get_api_provider_verify_kiln_copilot_api_key.md|
